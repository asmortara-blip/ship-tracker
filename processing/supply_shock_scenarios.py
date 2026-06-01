"""supply_shock_scenarios.py — pure-function what-if shock engine on top
of ``processing.port_supply_lines``.

Answers "what if Shanghai went offline?" / "what if Panama lost 50%
throughput?" / "what if oil demand spiked?" by re-deriving the port
supply-lines chain under a pre-canned shock and ranking the affected
companies by deficit-day delta vs the baseline.

Every shock is composable through one dataclass — ``ShockScenario`` —
that carries three orthogonal levers:

  * ``port_throughput_multipliers``  — per-LOCODE capacity multiplier;
                                       <1.0 worsens the port's supply
                                       deficit, >1.0 eases it.
  * ``route_disabled_locodes``       — any chain whose touching routes
                                       all flow through one of these
                                       LOCODEs is dropped.
  * ``commodity_demand_shocks``      — per-hs-category demand multiplier;
                                       scales the cargo-mix shares that
                                       drive the per-company exposure
                                       weights.

The engine does NOT recompute chains from the upstream registries — it
clones the baseline chain list returned by
``build_port_supply_chains`` and mutates per-chain state, so the shock
is cheap and deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable

from data.quality import DataSource

from processing.port_supply_lines import (
    CompanyExposure,
    PortExposureChain,
    PortSupplyState,
    build_port_supply_chains,
    _severity_label,
)


__all__ = [
    "ShockScenario",
    "CompanyScenarioImpact",
    "ScenarioImpactReport",
    "BASE_THROUGHPUT_DAYS",
    "BUILTIN_SCENARIOS",
    "apply_scenario",
    "compare_scenario_impact",
    "get_scenario",
    "SUPPLY_SHOCK_SOURCE",
]


# Treated as the baseline "days of supply buffer" that a port's
# throughput multiplier scales against. A 0.5× capacity shock removes
# half of this buffer (10 days) from every affected port's
# supply_deficit_days; a 2.0× shock adds 20 days. Held constant across
# ports so the property test "mult=0.5 always lowers, mult=2.0 always
# raises" holds for every port regardless of its current state.
BASE_THROUGHPUT_DAYS: float = 20.0


SUPPLY_SHOCK_SOURCE = DataSource.modeled(
    "Supply Shock Scenarios",
    notes=(
        "Pre-canned what-if shocks layered on top of port_supply_lines. "
        "Mutates a cloned chain list — does not call upstream registries "
        "again so the shock is deterministic and cheap to compose."
    ),
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ShockScenario:
    """A composable pre-canned shock.

    All three lever dicts default empty — the empty-everything scenario
    is the identity shock and the engine guarantees it leaves chains
    untouched (pinned by ``test_empty_scenario_is_identity``).
    """

    name: str
    description: str
    port_throughput_multipliers: dict[str, float] = field(default_factory=dict)
    route_disabled_locodes: list[str] = field(default_factory=list)
    commodity_demand_shocks: dict[str, float] = field(default_factory=dict)


@dataclass
class CompanyScenarioImpact:
    """Per-ticker shock impact rolled up across every port the ticker
    touches in either the baseline or scenario chains."""

    ticker: str
    baseline_total_deficit: float       # sum of -min(0, deficit_days)*exposure
    scenario_total_deficit: float
    total_deficit_delta: float          # scenario - baseline (positive = worse)
    ports_now_critical: list[str]       # locodes that crossed into Critical Deficit
    ports_now_recovered: list[str]      # locodes that left Critical Deficit
    n_ports_touched: int


@dataclass
class ScenarioImpactReport:
    """Output of ``compare_scenario_impact`` — per-company deltas plus
    scenario-level summary stats."""

    scenario_name: str
    container_type: str
    company_impacts: list[CompanyScenarioImpact] = field(default_factory=list)
    n_ports_baseline: int = 0
    n_ports_scenario: int = 0
    n_chains_dropped: int = 0
    summary: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ticker_deficit_score(chains: Iterable[PortExposureChain]) -> dict[str, float]:
    """Sum, per ticker, of ``exposure_weight × max(0, -deficit_days)``
    across every port the ticker is exposed to.

    Mirrors ``CompanyPortFootprint.deficit_weighted_score`` so the
    baseline-vs-scenario delta is comparable to what the inverted view
    already publishes.
    """
    score: dict[str, float] = {}
    for chain in chains:
        bucket = max(0.0, -float(chain.port.supply_deficit_days))
        if bucket == 0.0:
            continue
        for ce in chain.exposed_companies:
            score[ce.ticker] = score.get(ce.ticker, 0.0) + ce.exposure_weight * bucket
    return score


def _ticker_critical_ports(chains: Iterable[PortExposureChain]) -> dict[str, set[str]]:
    """Per ticker, the set of LOCODEs in Critical Deficit that the ticker
    has any exposure to."""
    out: dict[str, set[str]] = {}
    for chain in chains:
        if chain.port.severity_label != "Critical Deficit":
            continue
        for ce in chain.exposed_companies:
            out.setdefault(ce.ticker, set()).add(chain.port.locode)
    return out


def _ticker_touched_ports(chains: Iterable[PortExposureChain]) -> dict[str, set[str]]:
    """Per ticker, every LOCODE the ticker has any exposure to."""
    out: dict[str, set[str]] = {}
    for chain in chains:
        for ce in chain.exposed_companies:
            out.setdefault(ce.ticker, set()).add(chain.port.locode)
    return out


def _recompute_companies_from_commodity_totals(
    commodity_totals: dict[str, float],
    n_routes: int,
    top_n: int,
    route_names: list[str],
) -> tuple[list[CompanyExposure], list[tuple[str, float]]]:
    """Re-derive ``exposed_companies`` + ``top_commodities`` from a
    (possibly reweighted) commodity-totals dict.

    Mirrors the inner formula in ``build_port_supply_chains`` so the
    shocked chain has the same shape as the baseline — no missing
    keys, same sort order, same rounding precision.
    """
    try:
        from processing.exposure_matrix import COMPANY_COMMODITY_EXPOSURE
    except Exception:  # pragma: no cover - defensive
        COMPANY_COMMODITY_EXPOSURE = {}  # type: ignore[assignment]

    n_routes = max(1, int(n_routes))
    company_exposure: dict[str, float] = {}
    company_via_commodities: dict[str, set[str]] = {}
    for ticker, company_weights in COMPANY_COMMODITY_EXPOSURE.items():
        weight = 0.0
        via: set[str] = set()
        for hs, total_cargo_weight in commodity_totals.items():
            avg_cargo = total_cargo_weight / n_routes
            cw = float(company_weights.get(hs, 0.0))
            if cw <= 0.0:
                continue
            weight += avg_cargo * cw
            via.add(hs)
        if weight > 0:
            company_exposure[ticker] = weight
            company_via_commodities[ticker] = via

    ranked = sorted(
        company_exposure.items(), key=lambda kv: kv[1], reverse=True
    )[:max(1, int(top_n))]
    exposed = [
        CompanyExposure(
            ticker=ticker,
            exposure_weight=round(weight, 6),
            via_commodities=sorted(company_via_commodities[ticker])[:5],
            via_routes=route_names[:5],
        )
        for ticker, weight in ranked
    ]
    top_commodities = sorted(
        commodity_totals.items(), key=lambda kv: kv[1], reverse=True
    )[:5]
    return exposed, top_commodities


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def apply_scenario(
    scenario: ShockScenario,
    *,
    container_type: str = "40FT_DRY",
    top_n_companies: int = 8,
    baseline_chains: list[PortExposureChain] | None = None,
) -> list[PortExposureChain]:
    """Return a new chain list reflecting ``scenario``'s shocks.

    The baseline chains are fetched from ``build_port_supply_chains``
    when ``baseline_chains`` is not supplied — callers driving a batch
    comparison (e.g. the CLI's ``--all``) should pass the cached
    baseline so the engine doesn't re-run the upstream join per
    scenario.

    Behaviour:

    1. **Throughput multipliers** shift each affected port's
       ``supply_deficit_days`` by ``(mult - 1.0) * BASE_THROUGHPUT_DAYS``
       and re-derive its severity label. Unknown LOCODEs are silently
       ignored.
    2. **Route disabling** drops every chain whose only touching routes
       all flow through a disabled LOCODE. Chains with at least one
       surviving route stay in the output (their exposure may be
       slightly stale, but the port itself is still operable).
    3. **Commodity demand shocks** multiply the per-route cargo-mix
       totals for each shocked HS category and re-derive
       ``exposed_companies`` + ``top_commodities`` from the rebuilt
       totals.

    The empty-everything scenario is the identity — the chain list
    returned by ``apply_scenario(ShockScenario(...))`` equals the
    baseline element-for-element.
    """
    if baseline_chains is None:
        baseline_chains = build_port_supply_chains(
            container_type=container_type,
            top_n_companies=top_n_companies,
        )

    shocked: list[PortExposureChain] = []
    disabled = set(scenario.route_disabled_locodes or [])
    mults = dict(scenario.port_throughput_multipliers or {})
    cshocks = dict(scenario.commodity_demand_shocks or {})

    # Lazy import — we need the route_registry to know which routes
    # touch each port for the route-disabling pass. We also need it to
    # count surviving routes per chain so commodity totals can be
    # re-normalised.
    try:
        from routes.route_registry import ROUTES
    except Exception:  # pragma: no cover - defensive
        ROUTES = []  # type: ignore[assignment]
    routes_by_port: dict[str, list] = {}
    for r in ROUTES:
        for lc in (
            getattr(r, "origin_locode", ""),
            getattr(r, "dest_locode", ""),
        ):
            routes_by_port.setdefault(lc, []).append(r)

    for chain in baseline_chains:
        # ── 1. Route-disabling: drop chain if every touching route is
        # killed by the shock.
        touching = routes_by_port.get(chain.port.locode, [])
        if touching and disabled:
            survivors = [
                r for r in touching
                if r.origin_locode not in disabled
                and r.dest_locode not in disabled
            ]
            if not survivors:
                continue
        else:
            survivors = touching

        # ── 2. Throughput multiplier — shift port deficit_days +
        # rebuild severity label.
        port = chain.port
        mult = mults.get(port.locode, 1.0)
        if mult != 1.0:
            new_deficit = port.supply_deficit_days + (
                float(mult) - 1.0
            ) * BASE_THROUGHPUT_DAYS
            new_port = replace(
                port,
                supply_deficit_days=float(new_deficit),
                severity_label=_severity_label(new_deficit),
            )
        else:
            new_port = port

        # ── 3. Commodity demand shocks — rescale top_commodities + re-derive
        # exposed_companies.
        if cshocks or (touching and len(survivors) != len(touching)):
            # Rebuild commodity totals from chain.top_commodities then
            # apply shocks. top_commodities is the snapshot of the
            # baseline mix; we treat it as the canonical seed so
            # re-derivation is stable.
            commodity_totals = {hs: float(w) for hs, w in chain.top_commodities}
            for hs, shock in cshocks.items():
                if hs in commodity_totals:
                    commodity_totals[hs] = commodity_totals[hs] * float(shock)
            n_routes = max(1, len(survivors) if survivors else 1)
            new_exposed, new_top = _recompute_companies_from_commodity_totals(
                commodity_totals=commodity_totals,
                n_routes=n_routes,
                top_n=max(top_n_companies, len(chain.exposed_companies) or 1),
                route_names=[r.name for r in survivors] if survivors
                            else chain.routes_touching,
            )
        else:
            new_exposed = list(chain.exposed_companies)
            new_top = list(chain.top_commodities)

        new_summary = (
            f"{new_port.name} ({new_port.locode}, {new_port.region}) — "
            f"{new_port.severity_label}: "
            f"{new_port.supply_deficit_days:+.1f}d on {new_port.container_type}; "
            f"{len(survivors) if survivors else len(touching)} route(s) touching; "
            f"{len(new_exposed)} companies exposed."
        )

        shocked.append(PortExposureChain(
            port=new_port,
            exposed_companies=new_exposed,
            routes_touching=[r.name for r in survivors] if survivors
                            else list(chain.routes_touching),
            top_commodities=new_top,
            summary=new_summary,
        ))

    shocked.sort(key=lambda c: c.port.supply_deficit_days)
    return shocked


def compare_scenario_impact(
    baseline_chains: list[PortExposureChain],
    scenario_chains: list[PortExposureChain],
    *,
    scenario_name: str = "",
    container_type: str = "40FT_DRY",
) -> ScenarioImpactReport:
    """Rank the company-level impact of a scenario.

    ``total_deficit_delta`` is ``scenario_score - baseline_score`` where
    each score is the deficit-weighted exposure sum (mirrors
    ``CompanyPortFootprint.deficit_weighted_score``). Positive deltas
    mean the scenario made the company's port exposure WORSE; negative
    deltas mean it eased.

    Every ticker that appears in either chain set is included in the
    output — count is invariant under the scenario (no companies appear
    or disappear), only the delta changes.
    """
    base_scores = _ticker_deficit_score(baseline_chains)
    scen_scores = _ticker_deficit_score(scenario_chains)
    base_crit = _ticker_critical_ports(baseline_chains)
    scen_crit = _ticker_critical_ports(scenario_chains)
    base_touched = _ticker_touched_ports(baseline_chains)
    scen_touched = _ticker_touched_ports(scenario_chains)

    all_tickers = (
        set(base_scores)
        | set(scen_scores)
        | set(base_touched)
        | set(scen_touched)
    )

    impacts: list[CompanyScenarioImpact] = []
    for ticker in sorted(all_tickers):
        b = float(base_scores.get(ticker, 0.0))
        s = float(scen_scores.get(ticker, 0.0))
        crit_b = base_crit.get(ticker, set())
        crit_s = scen_crit.get(ticker, set())
        touched = base_touched.get(ticker, set()) | scen_touched.get(ticker, set())
        impacts.append(CompanyScenarioImpact(
            ticker=ticker,
            baseline_total_deficit=round(b, 6),
            scenario_total_deficit=round(s, 6),
            total_deficit_delta=round(s - b, 6),
            ports_now_critical=sorted(crit_s - crit_b),
            ports_now_recovered=sorted(crit_b - crit_s),
            n_ports_touched=len(touched),
        ))

    # Worst-first by delta (largest positive delta = most-hurt company).
    impacts.sort(key=lambda i: i.total_deficit_delta, reverse=True)

    n_dropped = max(0, len(baseline_chains) - len(scenario_chains))
    worst = impacts[0] if impacts else None
    if worst is not None and abs(worst.total_deficit_delta) > 0:
        worst_blurb = (
            f"worst hit: {worst.ticker} "
            f"(Δ {worst.total_deficit_delta:+.3f})"
        )
    else:
        worst_blurb = "no measurable company-level impact"
    summary = (
        f"{scenario_name or 'scenario'} on {container_type}: "
        f"{len(baseline_chains)} baseline ports → "
        f"{len(scenario_chains)} after shock "
        f"({n_dropped} dropped); "
        f"{len(impacts)} tickers evaluated; {worst_blurb}."
    )

    return ScenarioImpactReport(
        scenario_name=scenario_name,
        container_type=container_type,
        company_impacts=impacts,
        n_ports_baseline=len(baseline_chains),
        n_ports_scenario=len(scenario_chains),
        n_chains_dropped=n_dropped,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# BUILTIN_SCENARIOS — pre-canned shocks for the CLI's --all path.
# ---------------------------------------------------------------------------
#
# Every multiplier / disabled-locode here is grounded in the publicly-known
# choke-point geography that the route registry already encodes. Locodes
# absent from the registry are silently no-ops, which is fine — the CLI
# will still report a (likely small) impact and the property test
# "at least one builtin produces non-zero impact" will fire on the live
# ones.

BUILTIN_SCENARIOS: list[ShockScenario] = [
    ShockScenario(
        name="Suez Closure 100%",
        description=(
            "Suez Canal fully closed — every Asia↔Europe lane via Suez "
            "(Shanghai/Ningbo → Rotterdam/Antwerp, Jebel Ali → Rotterdam) "
            "rerouted around the Cape; Med-hub ports lose flow."
        ),
        port_throughput_multipliers={
            "GRPIR": 0.4,   # Piraeus — Med transhipment hub
            "MATNM": 0.6,   # Tanger Med — North Africa transhipment
            "AEJEA": 0.7,   # Jebel Ali — Suez-bound Gulf hub
            "LKCMB": 0.6,   # Colombo — South Asia feeder for Suez routes
        },
        route_disabled_locodes=[],  # Routes survive (via Cape), only capacity drops.
        commodity_demand_shocks={},
    ),
    ShockScenario(
        name="Shanghai 50% Throughput",
        description=(
            "Shanghai operating at half capacity — typhoon, COVID-style "
            "lockdown, or major berthing outage. Trans-Pacific EB, "
            "Asia-Europe, and every Shanghai-origin lane is degraded."
        ),
        port_throughput_multipliers={"CNSHA": 0.5},
        route_disabled_locodes=[],
        commodity_demand_shocks={},
    ),
    ShockScenario(
        name="Panama Drought 30%",
        description=(
            "Panama Canal water-level drought knocks 30% off effective "
            "throughput; East-Coast / South-American routes that "
            "transit Panama see reduced container availability "
            "(modelled here as a hit to NY/NJ + Savannah feeders)."
        ),
        port_throughput_multipliers={
            "USNYC": 0.7,
            "USSAV": 0.7,
            "BRSAO": 0.85,  # Santos sees reduced Atlantic↔Pacific flow.
        },
        route_disabled_locodes=[],
        commodity_demand_shocks={},
    ),
    ShockScenario(
        name="Red Sea Tensions",
        description=(
            "Sustained Red Sea attacks — Suez transits run at 75% "
            "capacity; every Med + Gulf hub feeling the drag."
        ),
        port_throughput_multipliers={
            "GRPIR": 0.75,
            "MATNM": 0.75,
            "AEJEA": 0.75,
            "LKCMB": 0.75,
        },
        route_disabled_locodes=[],
        commodity_demand_shocks={},
    ),
    ShockScenario(
        name="China Quarantine 30d",
        description=(
            "All Chinese mainland container ports at 25% capacity for "
            "30 days — global supply chain shock. Every CN* LOCODE in "
            "the registry hit; Hong Kong + Taiwan ride the spillover."
        ),
        port_throughput_multipliers={
            "CNSHA": 0.25,
            "CNNBO": 0.25,
            "CNSZN": 0.25,
            "CNTAO": 0.25,
            "CNTXG": 0.25,
            "HKHKG": 0.6,
            "TWKHH": 0.7,
        },
        route_disabled_locodes=[],
        commodity_demand_shocks={"electronics": 1.4, "apparel": 1.3},
    ),
    ShockScenario(
        name="Oil Crisis Demand Spike",
        description=(
            "Sustained oil-price spike → industrial demand shifts; "
            "chemicals + metals carriage up 50%, electronics + "
            "apparel demand contracts 20%. No port outage."
        ),
        port_throughput_multipliers={},
        route_disabled_locodes=[],
        commodity_demand_shocks={
            "chemicals": 1.5,
            "metals": 1.5,
            "electronics": 0.8,
            "apparel": 0.8,
        },
    ),
]


def get_scenario(name: str) -> ShockScenario | None:
    """Lookup helper — case-insensitive name match against
    ``BUILTIN_SCENARIOS``. Returns None if not found."""
    if not name:
        return None
    needle = name.strip().lower()
    for s in BUILTIN_SCENARIOS:
        if s.name.strip().lower() == needle:
            return s
    return None
