"""
Geopolitical Risk Monitor

Tracks current (2025-2026) geopolitical events that affect global shipping
routes, chokepoints, freight rates, and trade volumes.

Each GeopoliticalEvent carries:
  - Affected route IDs and chokepoint names
  - Rate/volume impact estimates (%)
  - Subjective probability of materialisation
  - Expected-value impact (rate_impact * probability)
  - Resolution timeline

Key functions:
  compute_geopolitical_score(route_id)    -> float [0, 1]
  get_route_risk_events(route_id)         -> list[GeopoliticalEvent]
  compute_expected_rate_impact(route_id)  -> float
  get_chokepoint_exposure(route_id)       -> dict[str, float]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from loguru import logger


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class GeopoliticalEvent:
    event_id: str
    title: str
    description: str
    risk_level: str                       # "CRITICAL" | "HIGH" | "MODERATE" | "LOW"
    affected_routes: List[str]            # route_ids
    affected_chokepoints: List[str]
    rate_impact_pct: float                # estimated % freight rate change
    volume_impact_pct: float              # estimated % trade volume change
    probability: float                    # 0-1 subjective probability of materialising
    expected_value_impact: float          # rate_impact_pct * probability (computed at init)
    resolution_timeline: str             # "Days" | "Weeks" | "Months" | "Ongoing"
    last_updated: str                     # ISO date string


def _make_event(
    event_id: str,
    title: str,
    description: str,
    risk_level: str,
    affected_routes: List[str],
    affected_chokepoints: List[str],
    rate_impact_pct: float,
    volume_impact_pct: float,
    probability: float,
    resolution_timeline: str,
    last_updated: str,
) -> GeopoliticalEvent:
    """Factory that auto-computes expected_value_impact."""
    return GeopoliticalEvent(
        event_id=event_id,
        title=title,
        description=description,
        risk_level=risk_level,
        affected_routes=affected_routes,
        affected_chokepoints=affected_chokepoints,
        rate_impact_pct=rate_impact_pct,
        volume_impact_pct=volume_impact_pct,
        probability=probability,
        expected_value_impact=round(rate_impact_pct * probability, 2),
        resolution_timeline=resolution_timeline,
        last_updated=last_updated,
    )


# ---------------------------------------------------------------------------
# Current geopolitical risk events (2025-2026, realistic data)
# ---------------------------------------------------------------------------

CURRENT_RISK_EVENTS: List[GeopoliticalEvent] = [

    # 1 ─ Red Sea / Houthi disruptions (ONGOING, highest impact)
    _make_event(
        event_id="red_sea_houthi_2025",
        title="Red Sea / Houthi Shipping Disruptions",
        description=(
            "Houthi militants continue drone and missile attacks on commercial vessels "
            "transiting the Red Sea and Gulf of Aden. Major container carriers are "
            "rerouting via Cape of Good Hope, adding 10-14 days and $400-800/FEU in "
            "fuel costs. Transit volumes through Suez Canal down ~60% from 2023 peak."
        ),
        risk_level="CRITICAL",
        affected_routes=[
            "asia_europe",
            "ningbo_europe",
            "south_asia_to_europe",
            "middle_east_to_europe",
            "med_hub_to_asia",
        ],
        affected_chokepoints=["Bab-el-Mandeb (Red Sea)", "Suez Canal"],
        rate_impact_pct=35.0,
        volume_impact_pct=-18.0,
        probability=0.92,
        resolution_timeline="Ongoing",
        last_updated="2026-03-01",
    ),

    # 2 ─ US-China tariff escalation
    _make_event(
        event_id="us_china_tariffs_2025",
        title="US-China Tariff Escalation",
        description=(
            "The US has imposed tariffs of 60-145% on Chinese goods across multiple "
            "categories, with China retaliating on US exports. Front-loading shipments "
            "created a rate spike in H1 2025; a subsequent demand drop is weighing on "
            "transpacific volumes. Long-term supply-chain relocation to Vietnam, Mexico, "
            "and India is accelerating."
        ),
        risk_level="HIGH",
        affected_routes=[
            "transpacific_eb",
            "transpacific_wb",
            "longbeach_to_asia",
            "sea_transpacific_eb",
        ],
        affected_chokepoints=["Taiwan Strait"],
        rate_impact_pct=25.0,
        volume_impact_pct=-12.0,
        probability=0.88,
        resolution_timeline="Ongoing",
        last_updated="2026-03-01",
    ),

    # 3 ─ Panama Canal water levels / capacity constraints
    _make_event(
        event_id="panama_canal_water_2025",
        title="Panama Canal Drought & Capacity Constraints",
        description=(
            "Water levels in Gatun Lake remain below historical averages following the "
            "2024 El Nino drought. While draft restrictions have eased from the 2024 "
            "lows, booking queues and neo-panamax slot availability remain constrained. "
            "Risk of renewed restrictions if La Nina conditions intensify in late 2025."
        ),
        risk_level="MODERATE",
        affected_routes=[
            "transpacific_eb",
            "us_east_south_america",
        ],
        affected_chokepoints=["Panama Canal"],
        rate_impact_pct=12.0,
        volume_impact_pct=-8.0,
        probability=0.45,
        resolution_timeline="Months",
        last_updated="2026-03-01",
    ),

    # 4 ─ Taiwan Strait military tensions
    _make_event(
        event_id="taiwan_strait_tensions_2025",
        title="Taiwan Strait Military Tensions",
        description=(
            "PLA military exercises around Taiwan have increased in frequency and scale. "
            "Periodic flight information region (FIR) closure notices and naval activity "
            "disrupt vessel scheduling. A full blockade scenario would affect 26% of "
            "global container trade. Insurance premiums for Taiwan Strait transits "
            "have risen 30-40%."
        ),
        risk_level="HIGH",
        affected_routes=[
            "transpacific_eb",
            "transpacific_wb",
            "intra_asia_china_sea",
            "intra_asia_china_japan",
            "sea_transpacific_eb",
        ],
        affected_chokepoints=["Taiwan Strait"],
        rate_impact_pct=40.0,
        volume_impact_pct=-30.0,
        probability=0.22,
        resolution_timeline="Ongoing",
        last_updated="2026-03-01",
    ),

    # 5 ─ Russian sanctions on shipping
    _make_event(
        event_id="russia_sanctions_shipping_2025",
        title="Russian Sanctions & Shadow Fleet Enforcement",
        description=(
            "Expanded G7 and EU sanctions now target the Russian shadow tanker fleet "
            "and insurers enabling sanctioned oil shipments. Port state control "
            "inspections in European ports have intensified. Legitimate carriers face "
            "increased compliance costs and reputational risk from inadvertent exposure."
        ),
        risk_level="MODERATE",
        affected_routes=[
            "transatlantic",
            "north_africa_to_europe",
        ],
        affected_chokepoints=["Danish Straits"],
        rate_impact_pct=8.0,
        volume_impact_pct=-5.0,
        probability=0.70,
        resolution_timeline="Ongoing",
        last_updated="2026-03-01",
    ),

    # 6 ─ US West Coast port labor disputes
    _make_event(
        event_id="us_west_coast_labor_2025",
        title="US West Coast Port Labor Disputes (ILWU)",
        description=(
            "ILWU contract negotiations for automation provisions remain contentious. "
            "Sporadic work-to-rule actions and slowdowns have caused congestion at "
            "Los Angeles, Long Beach, and Seattle. A full strike is not the base case "
            "but shippers are pre-emptively routing via US East Coast and Canadian ports."
        ),
        risk_level="MODERATE",
        affected_routes=[
            "transpacific_eb",
            "sea_transpacific_eb",
            "longbeach_to_asia",
        ],
        affected_chokepoints=[],
        rate_impact_pct=15.0,
        volume_impact_pct=-10.0,
        probability=0.35,
        resolution_timeline="Months",
        last_updated="2026-03-01",
    ),

    # 7 ─ Suez Canal heightened chokepoint risk (beyond Houthi direct attacks)
    _make_event(
        event_id="suez_chokepoint_risk_2025",
        title="Suez Canal Structural Chokepoint Risk",
        description=(
            "Even if Houthi attacks cease, the Suez Canal's single-lane architecture "
            "and Egyptian political fragility represent structural risk. Canal authority "
            "revenue is down 60%+ due to diversions, straining maintenance budgets. "
            "Any renewed political instability in Egypt could close the canal entirely."
        ),
        risk_level="HIGH",
        affected_routes=[
            "asia_europe",
            "ningbo_europe",
            "middle_east_to_europe",
            "med_hub_to_asia",
            "south_asia_to_europe",
        ],
        affected_chokepoints=["Suez Canal"],
        rate_impact_pct=30.0,
        volume_impact_pct=-20.0,
        probability=0.30,
        resolution_timeline="Months",
        last_updated="2026-03-01",
    ),

    # 8 ─ South China Sea territorial disputes
    _make_event(
        event_id="south_china_sea_2025",
        title="South China Sea Territorial Disputes",
        description=(
            "Escalating confrontations between Chinese Coast Guard and Philippine, "
            "Vietnamese, and Malaysian vessels at Scarborough Shoal and Second Thomas "
            "Shoal. US freedom-of-navigation operations have increased. Risk of an "
            "incident that disrupts intra-Asia trade through the Malacca Strait corridor."
        ),
        risk_level="MODERATE",
        affected_routes=[
            "intra_asia_china_sea",
            "sea_transpacific_eb",
            "asia_europe",
        ],
        affected_chokepoints=["Strait of Malacca"],
        rate_impact_pct=10.0,
        volume_impact_pct=-6.0,
        probability=0.25,
        resolution_timeline="Ongoing",
        last_updated="2026-03-01",
    ),

    # 9 ─ India-Pakistan trade tensions
    _make_event(
        event_id="india_pakistan_tensions_2025",
        title="India-Pakistan Trade & Diplomatic Tensions",
        description=(
            "Renewed hostilities along the Line of Control and suspension of bilateral "
            "trade agreements have disrupted South Asian supply chains. Pakistan port "
            "access restrictions for Indian-flagged vessels add friction. Impact on "
            "global shipping is contained but affects Colombo transshipment volumes."
        ),
        risk_level="LOW",
        affected_routes=[
            "south_asia_to_europe",
            "middle_east_to_asia",
        ],
        affected_chokepoints=["Strait of Hormuz"],
        rate_impact_pct=5.0,
        volume_impact_pct=-3.0,
        probability=0.40,
        resolution_timeline="Months",
        last_updated="2026-03-01",
    ),

    # 10 ─ EU Carbon Border Adjustment Mechanism (CBAM)
    _make_event(
        event_id="eu_cbam_shipping_2026",
        title="EU Carbon Border Adjustment & FuelEU Maritime",
        description=(
            "EU's FuelEU Maritime regulation and ETS (Emissions Trading System) now "
            "apply to 50% of emissions from voyages to/from EU ports, rising to 100% "
            "by 2026. Carriers face EUA costs of EUR 45-80/tonne CO2. Long-term, "
            "this accelerates green fuel adoption but near-term adds 3-8% to "
            "Asia-Europe operating costs for non-compliant vessels."
        ),
        risk_level="LOW",
        affected_routes=[
            "asia_europe",
            "ningbo_europe",
            "transatlantic",
            "europe_south_america",
            "north_africa_to_europe",
            "med_hub_to_asia",
            "middle_east_to_europe",
            "south_asia_to_europe",
        ],
        affected_chokepoints=[],
        rate_impact_pct=6.0,
        volume_impact_pct=-2.0,
        probability=0.95,
        resolution_timeline="Ongoing",
        last_updated="2026-03-01",
    ),
]


# ---------------------------------------------------------------------------
# Risk level scoring helpers
# ---------------------------------------------------------------------------

_RISK_LEVEL_SCORE: dict[str, float] = {
    "CRITICAL": 1.00,
    "HIGH":     0.75,
    "MODERATE": 0.45,
    "LOW":      0.15,
}

_RISK_LEVEL_COLOR: dict[str, str] = {
    "CRITICAL": "#ef4444",
    "HIGH":     "#f97316",
    "MODERATE": "#f59e0b",
    "LOW":      "#10b981",
}


def get_risk_color(risk_level: str) -> str:
    """Return the hex color associated with a risk level string."""
    return _RISK_LEVEL_COLOR.get(risk_level, "#94a3b8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_route_risk_events(route_id: str) -> List[GeopoliticalEvent]:
    """Return all current geopolitical events that affect *route_id*."""
    events = [e for e in CURRENT_RISK_EVENTS if route_id in e.affected_routes]
    logger.debug("get_route_risk_events({}): {} events found", route_id, len(events))
    return events


def compute_geopolitical_score(route_id: str) -> float:
    """Return a composite geopolitical risk score in [0, 1] for *route_id*.

    Score = probability-weighted mean of (risk_level_score * probability)
    across all events that affect the route.  Capped at 1.0.
    """
    events = get_route_risk_events(route_id)
    if not events:
        logger.debug("compute_geopolitical_score({}): no events -> 0.0", route_id)
        return 0.0

    weighted_sum = 0.0
    weight_total = 0.0
    for ev in events:
        level_score = _RISK_LEVEL_SCORE.get(ev.risk_level, 0.20)
        # Weight by probability so low-probability events contribute less
        w = ev.probability
        weighted_sum += level_score * w
        weight_total += w

    if weight_total == 0.0:
        return 0.0

    score = min(1.0, weighted_sum / weight_total)
    logger.debug("compute_geopolitical_score({}): {:.3f}", route_id, score)
    return round(score, 4)


def compute_expected_rate_impact(route_id: str) -> float:
    """Return the probability-weighted average rate impact (%) for *route_id*.

    Computed as: sum(rate_impact_pct_i * probability_i) / n_events

    A positive value means rates are expected to be higher due to geopolitical
    risk; a negative value would indicate deflationary pressure.
    """
    events = get_route_risk_events(route_id)
    if not events:
        logger.debug("compute_expected_rate_impact({}): no events -> 0.0", route_id)
        return 0.0

    total = sum(ev.rate_impact_pct * ev.probability for ev in events)
    result = round(total / len(events), 2)
    logger.debug("compute_expected_rate_impact({}): {:.2f}%", route_id, result)
    return result


def get_chokepoint_exposure(route_id: str) -> dict:
    """Return a mapping of chokepoint name -> exposure score for *route_id*.

    Exposure score = max(risk_level_score * probability) across all events
    that reference that chokepoint and affect the given route.
    """
    events = get_route_risk_events(route_id)
    exposure: dict[str, float] = {}

    for ev in events:
        level_score = _RISK_LEVEL_SCORE.get(ev.risk_level, 0.20)
        contribution = round(level_score * ev.probability, 4)
        for cp in ev.affected_chokepoints:
            if cp not in exposure or contribution > exposure[cp]:
                exposure[cp] = contribution

    logger.debug(
        "get_chokepoint_exposure({}): {} chokepoints identified",
        route_id,
        len(exposure),
    )
    return exposure


# ---------------------------------------------------------------------------
# All-route summary helpers (used by the UI tab)
# ---------------------------------------------------------------------------

# Canonical list of all 17 route IDs (matches vulnerability_scorer.py)
ALL_ROUTE_IDS: List[str] = [
    "transpacific_eb",
    "transpacific_wb",
    "asia_europe",
    "ningbo_europe",
    "transatlantic",
    "sea_transpacific_eb",
    "middle_east_to_europe",
    "middle_east_to_asia",
    "south_asia_to_europe",
    "intra_asia_china_sea",
    "intra_asia_china_japan",
    "china_south_america",
    "europe_south_america",
    "med_hub_to_asia",
    "north_africa_to_europe",
    "us_east_south_america",
    "longbeach_to_asia",
]

# Human-readable names (mirrors vulnerability_scorer)
ROUTE_DISPLAY_NAMES: dict[str, str] = {
    "transpacific_eb":      "Trans-Pacific Eastbound",
    "transpacific_wb":      "Trans-Pacific Westbound",
    "asia_europe":          "Asia-Europe (Suez)",
    "ningbo_europe":        "Ningbo-Europe",
    "transatlantic":        "Transatlantic",
    "sea_transpacific_eb":  "SE Asia-Trans-Pacific EB",
    "middle_east_to_europe": "Middle East-Europe",
    "middle_east_to_asia":  "Middle East-Asia",
    "south_asia_to_europe": "South Asia-Europe",
    "intra_asia_china_sea": "Intra-Asia: China-SE Asia",
    "intra_asia_china_japan": "Intra-Asia: China-Japan/Korea",
    "china_south_america":  "China-South America",
    "europe_south_america": "Europe-South America",
    "med_hub_to_asia":      "Mediterranean Hub-Asia",
    "north_africa_to_europe": "North Africa-Europe",
    "us_east_south_america": "US East-South America",
    "longbeach_to_asia":    "Long Beach-Asia",
}


def get_all_route_scores() -> list:
    """Return a list of (route_id, display_name, geo_score, expected_rate_impact) tuples."""
    results = []
    for rid in ALL_ROUTE_IDS:
        name = ROUTE_DISPLAY_NAMES.get(rid, rid)
        score = compute_geopolitical_score(rid)
        rate_impact = compute_expected_rate_impact(rid)
        top_events = get_route_risk_events(rid)
        top_event_title = top_events[0].title if top_events else "—"
        results.append((rid, name, score, rate_impact, top_event_title))
    return results
