"""tariff_analyzer.py — US-China trade policy tariff sensitivity analysis.

Estimates how changes in import tariff levels ripple through to container
shipping volumes and freight rates on each lane, using price-elasticity of
demand assumptions derived from academic trade-flow research.
"""
from __future__ import annotations

from dataclasses import dataclass


# ── Tariff exposure configuration ────────────────────────────────────────────
# exposure_score: float in [0, 1] representing the fraction of a route's
#   volume that is directly exposed to US-China goods trade flows.
# exposure_label: qualitative description for display.

ROUTE_TARIFF_EXPOSURE: dict[str, dict] = {
    "transpacific_eb": {
        "exposure_score": 0.85,
        "exposure_label": "HIGH",
        "current_tariff_pct": 0.145,   # ~14.5% effective weighted average
        "notes": "Dominant US-China goods lane; highly sensitive to bilateral tariff policy",
    },
    "transpacific_wb": {
        "exposure_score": 0.75,
        "exposure_label": "HIGH",
        "current_tariff_pct": 0.025,   # low US export tariff baseline
        "notes": "Return leg; capacity driven by EB demand, so indirectly tariff-exposed",
    },
    "asia_europe": {
        "exposure_score": 0.40,
        "exposure_label": "MODERATE",
        "current_tariff_pct": 0.065,
        "notes": "Some transshipment of China-origin goods; partially exposed via EU trade policy",
    },
    "transatlantic": {
        "exposure_score": 0.10,
        "exposure_label": "LOW",
        "current_tariff_pct": 0.035,
        "notes": "Primarily intra-OECD trade; minimal direct US-China tariff exposure",
    },
    "sea_transpacific_eb": {
        "exposure_score": 0.60,
        "exposure_label": "MODERATE-HIGH",
        "current_tariff_pct": 0.100,
        "notes": "SE Asia origin — partially displaced China exports routed via Vietnam/Thailand",
    },
    "ningbo_europe": {
        "exposure_score": 0.40,
        "exposure_label": "MODERATE",
        "current_tariff_pct": 0.065,
        "notes": "Chinese export variant of Asia-Europe; exposed to EU anti-dumping actions",
    },
    "middle_east_to_europe": {
        "exposure_score": 0.15,
        "exposure_label": "LOW",
        "current_tariff_pct": 0.020,
        "notes": "Predominantly Gulf petrochemical and re-export flows; low China-content",
    },
    "middle_east_to_asia": {
        "exposure_score": 0.20,
        "exposure_label": "LOW",
        "current_tariff_pct": 0.015,
        "notes": "Energy and commodities-driven; tariff impact mostly indirect via demand",
    },
    "south_asia_to_europe": {
        "exposure_score": 0.25,
        "exposure_label": "LOW-MODERATE",
        "current_tariff_pct": 0.040,
        "notes": "Sri Lanka/India origin; benefits from tariff diversion away from China",
    },
    "intra_asia_china_sea": {
        "exposure_score": 0.30,
        "exposure_label": "LOW-MODERATE",
        "current_tariff_pct": 0.030,
        "notes": "Feeder and transshipment flows; indirectly affected by trunk-lane volume shifts",
    },
    "intra_asia_china_japan": {
        "exposure_score": 0.20,
        "exposure_label": "LOW",
        "current_tariff_pct": 0.020,
        "notes": "Bilateral China-Japan/Korea trade; governed by regional trade agreements",
    },
    "china_south_america": {
        "exposure_score": 0.35,
        "exposure_label": "MODERATE",
        "current_tariff_pct": 0.055,
        "notes": "Growing China-LATAM lane; LATAM tariffs lower but geopolitical risk rising",
    },
    "europe_south_america": {
        "exposure_score": 0.10,
        "exposure_label": "LOW",
        "current_tariff_pct": 0.025,
        "notes": "EU-Mercosur corridor; minimal US-China tariff linkage",
    },
    "med_hub_to_asia": {
        "exposure_score": 0.25,
        "exposure_label": "LOW-MODERATE",
        "current_tariff_pct": 0.040,
        "notes": "Return leg of Asia-Europe; volume correlates with EB demand",
    },
    "north_africa_to_europe": {
        "exposure_score": 0.10,
        "exposure_label": "LOW",
        "current_tariff_pct": 0.020,
        "notes": "Short-sea Med feeder; negligible US-China exposure",
    },
    "us_east_south_america": {
        "exposure_score": 0.15,
        "exposure_label": "LOW",
        "current_tariff_pct": 0.030,
        "notes": "Intra-Americas corridor; modest indirect sensitivity via US macro demand",
    },
    "longbeach_to_asia": {
        "exposure_score": 0.70,
        "exposure_label": "HIGH",
        "current_tariff_pct": 0.025,
        "notes": "SoCal return leg; mirrors EB tariff sensitivity through empty repositioning",
    },
}


# ── Elasticity parameters ─────────────────────────────────────────────────────
# Price elasticity of trade volume for US-China goods: -0.8
#   (8% volume decline per 10% tariff increase).
_VOLUME_ELASTICITY: float = -0.8

# Rate follow-through coefficient: freight rates respond to volume changes
# with a partial lag; empirically ~0.6 of the volume change passes through.
_RATE_FOLLOW_THROUGH: float = 0.6


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class TariffImpact:
    route_id: str
    route_name: str
    current_tariff_pct: float      # effective tariff on main cargo (fraction, e.g. 0.145)
    volume_impact_pct: float       # signed % change in route volume from tariff shock
    rate_impact_pct: float         # signed % change in freight rate from tariff shock
    net_opportunity_delta: float   # composite score change (negative = headwind)
    scenario: str                  # "CURRENT" | "ESCALATION_25" | "ESCALATION_50" | "REDUCTION_50"


# ── Analysis function ─────────────────────────────────────────────────────────

def analyze_tariff_sensitivity(
    route_results: list,
    tariff_shock_pct: float = 0.0,
) -> list[TariffImpact]:
    """Compute tariff-driven volume, rate, and opportunity impacts per route.

    Parameters
    ----------
    route_results:
        Iterable of objects with at least ``route_id`` and ``route_name``
        attributes (e.g. RouteProfitability or RouteEmissions instances),
        or plain dicts with those keys.  All 17 registered routes will be
        evaluated; routes not present in ``route_results`` are skipped.
    tariff_shock_pct:
        Incremental tariff change as a decimal fraction, e.g.:
          0.0   -> CURRENT (baseline, no additional shock)
          0.25  -> ESCALATION_25 (25 percentage-point tariff increase)
          0.50  -> ESCALATION_50
         -0.50  -> REDUCTION_50

    Returns
    -------
    list[TariffImpact]
        One entry per route in route_results, ordered to match the input
        sequence.
    """
    # Determine scenario label
    if tariff_shock_pct == 0.0:
        scenario = "CURRENT"
    elif tariff_shock_pct == 0.25:
        scenario = "ESCALATION_25"
    elif tariff_shock_pct == 0.50:
        scenario = "ESCALATION_50"
    elif tariff_shock_pct == -0.50:
        scenario = "REDUCTION_50"
    else:
        # Generic label for arbitrary shocks
        direction = "ESCALATION" if tariff_shock_pct > 0 else "REDUCTION"
        pct_label = int(abs(tariff_shock_pct) * 100)
        scenario = f"{direction}_{pct_label}"

    impacts: list[TariffImpact] = []

    for route in route_results:
        # Accept both attribute-style and dict-style route objects
        if isinstance(route, dict):
            route_id = route["route_id"]
            route_name = route["route_name"]
        else:
            route_id = route.route_id
            route_name = route.route_name

        exposure_cfg = ROUTE_TARIFF_EXPOSURE.get(route_id)
        if exposure_cfg is None:
            # Unknown route — emit a zero-impact entry so callers always get a result
            impacts.append(
                TariffImpact(
                    route_id=route_id,
                    route_name=route_name,
                    current_tariff_pct=0.0,
                    volume_impact_pct=0.0,
                    rate_impact_pct=0.0,
                    net_opportunity_delta=0.0,
                    scenario=scenario,
                )
            )
            continue

        exposure_score: float = exposure_cfg["exposure_score"]
        current_tariff_pct: float = exposure_cfg["current_tariff_pct"]

        # Volume impact: tariff_shock * exposure_score * elasticity
        # Result is a signed fraction (negative => volume contraction).
        volume_impact_pct = tariff_shock_pct * exposure_score * _VOLUME_ELASTICITY

        # Rate impact: rates follow volumes with partial pass-through
        rate_impact_pct = volume_impact_pct * _RATE_FOLLOW_THROUGH

        # Net opportunity delta: weighted composite of volume (40%) and rate (60%) signals
        net_opportunity_delta = (
            volume_impact_pct * 0.4 + rate_impact_pct * 0.6
        )

        impacts.append(
            TariffImpact(
                route_id=route_id,
                route_name=route_name,
                current_tariff_pct=current_tariff_pct,
                volume_impact_pct=round(volume_impact_pct, 6),
                rate_impact_pct=round(rate_impact_pct, 6),
                net_opportunity_delta=round(net_opportunity_delta, 6),
                scenario=scenario,
            )
        )

    return impacts
