"""
What-If Scenario Analysis Engine

Simulates the impact of macro shocks, geopolitical events, and demand shifts
on route opportunity scores and port conditions.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ── ScenarioInput ─────────────────────────────────────────────────────────────

@dataclass
class ScenarioInput:
    name: str                           # e.g. "Suez Canal Closure"
    bdi_shock: float = 0.0             # e.g. 0.40 = +40%
    fuel_shock: float = 0.0            # e.g. 0.50 = +50%
    pmi_shock: float = 0.0             # absolute, e.g. -5.0
    suez_closed: bool = False
    panama_closed: bool = False
    us_china_tariff_hike: float = 0.0  # e.g. 0.25 = 25%
    demand_shock: float = 0.0          # -0.3 to +0.3
    description: str = ""


# ── ScenarioResult ────────────────────────────────────────────────────────────

@dataclass
class ScenarioResult:
    scenario: ScenarioInput
    baseline_avg_opportunity: float
    scenario_avg_opportunity: float
    opportunity_delta: float                   # scenario_avg - baseline_avg
    route_impacts: list[dict] = field(default_factory=list)
    # each: route_id, route_name, baseline, scenario_score, delta, impact_reason
    port_impacts: list[dict] = field(default_factory=list)
    # each: port_locode, port_name, baseline, scenario_score, delta
    summary: str = ""
    risk_level: str = "LOW"                    # "LOW" / "MODERATE" / "HIGH" / "SEVERE"


# ── Predefined scenarios ───────────────────────────────────────────────────────

PREDEFINED_SCENARIOS: list[ScenarioInput] = [
    ScenarioInput(
        name="Suez Canal Closure",
        suez_closed=True,
        fuel_shock=0.15,
        bdi_shock=0.40,
        demand_shock=-0.05,
        description=(
            "Full Suez closure forces Asia-Europe traffic around Cape of Good Hope, "
            "adding ~14 transit days."
        ),
    ),
    ScenarioInput(
        name="Panama Canal Drought",
        panama_closed=True,
        bdi_shock=0.20,
        description=(
            "Severe drought reduces Panama Canal capacity, forcing US East Coast "
            "traffic via Suez or Cape Horn."
        ),
    ),
    ScenarioInput(
        name="US-China Trade War Escalation",
        us_china_tariff_hike=0.25,
        demand_shock=-0.15,
        bdi_shock=-0.10,
        description="Additional 25% tariffs reduce Trans-Pacific volumes by ~15%.",
    ),
    ScenarioInput(
        name="Global Manufacturing Boom",
        pmi_shock=5.0,
        demand_shock=0.20,
        bdi_shock=0.35,
        description=(
            "Synchronized global manufacturing recovery drives container demand surge."
        ),
    ),
    ScenarioInput(
        name="Oil Price Spike (+50%)",
        fuel_shock=0.50,
        bdi_shock=0.15,
        demand_shock=-0.08,
        description="WTI spikes to $150/bbl, increasing shipping costs 20-30%.",
    ),
    ScenarioInput(
        name="Global Recession",
        pmi_shock=-8.0,
        demand_shock=-0.35,
        bdi_shock=-0.45,
        description=(
            "Synchronized recession crushes global trade demand, freight rates collapse."
        ),
    ),
    ScenarioInput(
        name="Asia Manufacturing Shift",
        demand_shock=-0.10,
        bdi_shock=0.05,
        description=(
            "Production shifts from China to SE Asia/India — Trans-Pacific WB weakens, "
            "new SE Asia lanes strengthen."
        ),
    ),
    ScenarioInput(
        name="Peak Season Surge",
        demand_shock=0.25,
        bdi_shock=0.30,
        description=(
            "Pre-holiday inventory build drives container bookings 30% above seasonal norms."
        ),
    ),
]


# ── Internal helpers ───────────────────────────────────────────────────────────

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _route_scenario_score(route, scenario: ScenarioInput) -> tuple[float, str]:
    """Return (scenario_score, impact_reason) for a single RouteOpportunity."""
    route_id = getattr(route, "route_id", getattr(route, "id", ""))
    fbx = getattr(route, "fbx_index", "")
    transit_days = getattr(route, "transit_days", 0)

    score = getattr(route, "opportunity_score", 0.5)
    reasons: list[str] = []

    # Suez closure: FBX03/FBX04 lanes gain opportunity (Asia-Europe disruption)
    if scenario.suez_closed:
        if fbx in ("FBX03", "FBX04"):
            score += 0.20
            reasons.append("Suez closure disrupts Asia-Europe/Med lane (+opportunity via Cape reroute)")
        elif fbx in ("FBX01", "FBX02"):
            score += 0.05
            reasons.append("Minor Suez ripple on Trans-Pacific")

    # Panama closure: transpacific routes gain (capacity tightens)
    if scenario.panama_closed and "transpacific" in route_id:
        score += 0.15
        reasons.append("Panama closure reroutes Trans-Pacific traffic, tightening capacity")

    # BDI shock: ~25% weight in route opportunity
    if scenario.bdi_shock != 0.0:
        score += scenario.bdi_shock * 0.25
        direction = "rise" if scenario.bdi_shock > 0 else "fall"
        reasons.append(
            "BDI expected to "
            + direction
            + " "
            + str(abs(int(scenario.bdi_shock * 100)))
            + "%"
        )

    # US-China tariff: hurts transpacific routes
    if scenario.us_china_tariff_hike != 0.0 and "transpacific" in route_id:
        score -= scenario.us_china_tariff_hike * 0.30
        reasons.append(
            "US-China tariff hike "
            + "{:.0%}".format(scenario.us_china_tariff_hike)
            + " suppresses Trans-Pacific demand"
        )

    # Demand shock: ~50% weight
    if scenario.demand_shock != 0.0:
        score += scenario.demand_shock * 0.50
        direction = "surge" if scenario.demand_shock > 0 else "contraction"
        reasons.append(
            "Global demand "
            + direction
            + " "
            + "{:+.0%}".format(scenario.demand_shock)
        )

    # Fuel shock: long-haul penalty (transit_days > 20)
    if scenario.fuel_shock != 0.0 and transit_days > 20:
        score -= scenario.fuel_shock * 0.10
        reasons.append(
            "Fuel shock "
            + "{:+.0%}".format(scenario.fuel_shock)
            + " hits long-haul route margin"
        )

    impact_reason = "; ".join(reasons) if reasons else "No material impact from this scenario"
    return _clamp(score), impact_reason


def _risk_level(delta: float) -> str:
    abs_d = abs(delta)
    if abs_d > 0.25:
        return "SEVERE"
    if abs_d > 0.15:
        return "HIGH"
    if abs_d > 0.07:
        return "MODERATE"
    return "LOW"


def _build_summary(scenario: ScenarioInput, delta: float, risk_level: str) -> str:
    sign = "+" if delta > 0 else ""
    direction_word = "boost" if delta > 0 else "drag"
    primary_driver = (
        "canal disruption"
        if scenario.suez_closed or scenario.panama_closed
        else "macro and demand shifts"
    )
    return (
        scenario.name
        + " scenario projects a "
        + sign
        + "{:.0%}".format(delta)
        + " shift in average route opportunity. "
        + "The event introduces a "
        + direction_word
        + " across the tracked shipping network driven by "
        + primary_driver
        + ". "
        + "Risk is rated "
        + risk_level
        + "."
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def run_scenario(
    scenario: ScenarioInput,
    port_results: list,
    route_results: list,
) -> ScenarioResult:
    """Apply a scenario to current analysis results and return an impact report."""

    # ── Per-route impact ──────────────────────────────────────────────────────
    route_impacts: list[dict] = []
    baseline_scores: list[float] = []
    scenario_scores: list[float] = []

    for route in route_results:
        route_id = getattr(route, "route_id", getattr(route, "id", ""))
        route_name = getattr(route, "route_name", getattr(route, "name", route_id))
        baseline = getattr(route, "opportunity_score", 0.5)

        scenario_score, impact_reason = _route_scenario_score(route, scenario)
        delta = scenario_score - baseline

        route_impacts.append(
            {
                "route_id": route_id,
                "route_name": route_name,
                "baseline": baseline,
                "scenario_score": scenario_score,
                "delta": delta,
                "impact_reason": impact_reason,
            }
        )
        baseline_scores.append(baseline)
        scenario_scores.append(scenario_score)

    baseline_avg = sum(baseline_scores) / len(baseline_scores) if baseline_scores else 0.5
    scenario_avg = sum(scenario_scores) / len(scenario_scores) if scenario_scores else 0.5
    opportunity_delta = scenario_avg - baseline_avg

    # ── Port impacts ──────────────────────────────────────────────────────────
    port_impacts: list[dict] = []
    for pr in port_results:
        port_locode = getattr(pr, "locode", getattr(pr, "port_locode", ""))
        port_name = getattr(pr, "port_name", getattr(pr, "name", port_locode))
        baseline_demand = getattr(pr, "demand_score", 0.5)
        scenario_demand = _clamp(baseline_demand + scenario.demand_shock * 0.6)
        port_impacts.append(
            {
                "port_locode": port_locode,
                "port_name": port_name,
                "baseline": baseline_demand,
                "scenario_score": scenario_demand,
                "delta": scenario_demand - baseline_demand,
            }
        )

    risk = _risk_level(opportunity_delta)
    summary = _build_summary(scenario, opportunity_delta, risk)

    return ScenarioResult(
        scenario=scenario,
        baseline_avg_opportunity=baseline_avg,
        scenario_avg_opportunity=scenario_avg,
        opportunity_delta=opportunity_delta,
        route_impacts=route_impacts,
        port_impacts=port_impacts,
        summary=summary,
        risk_level=risk,
    )


def run_all_scenarios(
    port_results: list,
    route_results: list,
) -> list[ScenarioResult]:
    """Run all 8 predefined scenarios; return sorted by |opportunity_delta| descending."""
    results = [
        run_scenario(s, port_results, route_results) for s in PREDEFINED_SCENARIOS
    ]
    results.sort(key=lambda r: abs(r.opportunity_delta), reverse=True)
    return results
