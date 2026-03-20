"""
Emerging Trade Routes Analyzer

Climate change and geopolitics are opening new maritime trade corridors while
disrupting traditional ones. This module tracks the 8 most significant emerging
or revived routes in 2024-2030.

Routes covered:
  1. Northern Sea Route (NSR)          — Russia's Arctic coast
  2. Northwest Passage (NWP)           — Canadian Arctic archipelago
  3. Transpolar Route                  — Direct over North Pole (future)
  4. India-Middle East-Europe (IMEC)   — India–UAE–Israel–Europe corridor
  5. Trans-Caspian Route (TITR)        — China–Kazakhstan–Caspian–Europe
  6. Cape of Good Hope (Suez bypass)   — Revived 2024 due to Houthi attacks
  7. Neo-Panamax / Panama Expansion    — Larger-vessel Suez competitor
  8. East Africa Corridor              — Asia–East Africa emerging market

Key function:
  compute_route_viability(route, freight_rate) -> dict
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class EmergingRoute:
    route_id: str
    route_name: str
    status: str                         # OPERATIONAL | PILOT | DEVELOPING | FUTURE
    origin: str
    destination: str
    distance_nm: int                    # Nautical miles for this route
    transit_days_summer: float          # Transit days in summer / good season
    transit_days_winter: Optional[float]  # None = route closed in winter
    vs_suez_saving_nm: Optional[int]    # Nautical miles saved vs Suez (Arctic routes)
    vs_suez_saving_days: Optional[float]  # Days saved vs Suez routing
    ice_class_required: str             # e.g. "None", "Ice Class 1A", "Polar Code"
    current_annual_vessels: int         # Approximate annual transits as of ~2024
    projected_2030_vessels: int         # Projected annual transits by 2030
    key_obstacle: str
    key_enabler: str
    rate_premium_pct: float             # % premium over standard Suez/Panama rate
    co2_per_teu: float                  # kg CO2 per TEU per nautical mile (approx)
    geopolitical_risk_score: float      # 0.0 (none) to 1.0 (extreme)
    economic_viability_score: float     # 0.0 (not viable) to 1.0 (fully viable)


# ---------------------------------------------------------------------------
# Route list
# ---------------------------------------------------------------------------

EMERGING_ROUTES: list[EmergingRoute] = [

    # 1 ── Northern Sea Route (NSR) ─────────────────────────────────────────
    # Russia's northern coast: Murmansk → Bering Strait along Siberia.
    # 12,800 nm vs 21,000 nm via Suez/Malacca (Asia→Europe) — 39% shorter.
    # Summer (Jul–Oct): navigable with icebreaker escort; winter: closed or
    # icebreaker-only at enormous cost. Russia charges $200k+ escort fees.
    # Post-2022 Ukraine sanctions have caused Western carriers to exit.
    # LNG tanker demand (Yamal LNG, Arctic LNG 2) driving vessel count growth.
    EmergingRoute(
        route_id="northern_sea_route",
        route_name="Northern Sea Route (NSR)",
        status="OPERATIONAL",
        origin="Yokohama / Shanghai",
        destination="Rotterdam / Hamburg",
        distance_nm=12_800,
        transit_days_summer=19.0,
        transit_days_winter=None,           # Closed for standard cargo; icebreaker-only
        vs_suez_saving_nm=8_200,            # 21,000 nm Suez vs 12,800 nm NSR
        vs_suez_saving_days=9.0,
        ice_class_required="Polar Code PC3 / Ice Class 1A Super",
        current_annual_vessels=45,
        projected_2030_vessels=210,
        key_obstacle=(
            "Russian territorial control; $200k+ icebreaker escort fees; war insurance "
            "surcharges; Western sanctions post-Ukraine; ice-class fleet scarcity"
        ),
        key_enabler=(
            "Arctic ice retreat advancing faster than IPCC models; LNG tanker demand "
            "(Yamal LNG, Arctic LNG 2); Russian Arctic development strategy"
        ),
        rate_premium_pct=38.0,              # Higher insurance, escort fees, ice-class capex
        co2_per_teu=0.021,                  # Lower distance but heavier fuel burn in ice
        geopolitical_risk_score=0.87,       # Russia control + sanctions environment
        economic_viability_score=0.42,
    ),

    # 2 ── Northwest Passage (NWP) ──────────────────────────────────────────
    # Through Canadian Arctic archipelago.
    # Asia → East Coast US: ~14,500 nm vs ~23,800 nm via Panama (~39% shorter
    # for specific origin pairs; distance advantage varies).
    # Currently shallow (<15 m at key points): bars ultra-large vessels.
    # Only 10-15 cargo transits/year; mostly research/tourism.
    # Climate change is opening summer windows faster than predicted (2040 models
    # showing near-ice-free summers by 2035 in some projections).
    EmergingRoute(
        route_id="northwest_passage",
        route_name="Northwest Passage (NWP)",
        status="PILOT",
        origin="Shanghai / Tokyo",
        destination="New York / Halifax",
        distance_nm=14_500,
        transit_days_summer=22.0,
        transit_days_winter=None,           # Impassable in winter
        vs_suez_saving_nm=None,             # Not a Suez alternative; vs Panama
        vs_suez_saving_days=None,
        ice_class_required="Ice Class 1A Super / PC 4",
        current_annual_vessels=12,
        projected_2030_vessels=65,
        key_obstacle=(
            "Draft restrictions (<15 m): bars ultra-large vessels; uncharted hazards; "
            "Canadian sovereignty claims; no rescue/port infrastructure; "
            "US-Canada vs Russia governance dispute"
        ),
        key_enabler=(
            "Arctic ice retreat; potential 25% route shortening for Asia-East Coast US; "
            "Canadian Coast Guard investment; tourism demand funding icebreaker hours"
        ),
        rate_premium_pct=52.0,              # Very high risk/infrastructure premium
        co2_per_teu=0.019,
        geopolitical_risk_score=0.38,       # Canada stable but route disputed
        economic_viability_score=0.28,
    ),

    # 3 ── Transpolar Route ─────────────────────────────────────────────────
    # Direct over the North Pole when Arctic is fully ice-free in summer.
    # Shanghai → New York: ~6,500 nm vs ~14,000 nm via Panama.
    # Economically viable window: 2040-2050 timeframe based on current melt rates.
    # No infrastructure exists; theoretical routing only.
    EmergingRoute(
        route_id="transpolar_route",
        route_name="Transpolar Route (North Pole Direct)",
        status="FUTURE",
        origin="Shanghai",
        destination="New York",
        distance_nm=6_500,
        transit_days_summer=11.0,           # Theoretical; requires full ice-free Arctic
        transit_days_winter=None,           # Not viable
        vs_suez_saving_nm=None,
        vs_suez_saving_days=None,
        ice_class_required="PC1 (Polar Class 1 — highest) until ice-free Arctic achieved",
        current_annual_vessels=0,
        projected_2030_vessels=0,           # Zero by 2030; viable ~2040-2050
        key_obstacle=(
            "Arctic not yet ice-free in summer; no search-and-rescue infrastructure; "
            "no ports or bunkering facilities; total absence of navigational aids; "
            "requires full-Arctic governance treaty"
        ),
        key_enabler=(
            "Climate models project seasonally ice-free Arctic by 2040-2060; "
            "54% distance saving vs Panama would generate massive economic incentive; "
            "autonomous shipping tech could reduce crew risk"
        ),
        rate_premium_pct=0.0,               # Not priced yet
        co2_per_teu=0.014,                  # Theoretical — shortest path
        geopolitical_risk_score=0.45,       # Unresolved Arctic sovereignty
        economic_viability_score=0.05,      # 10-15 years away
    ),

    # 4 ── India-Middle East-Europe Economic Corridor (IMEC) ────────────────
    # G20 MOU signed September 2023.
    # India (Mundra/JNPT) → UAE (Fujairah) → Saudi Arabia → Jordan
    # → Israel (Haifa) → Greece (Piraeus) → Europe.
    # Claimed 40% faster than Suez for specific origin-destination pairs.
    # Rail + sea multimodal corridor. Infrastructure investment underway.
    # Gaza conflict has significantly complicated the Israel hub component.
    EmergingRoute(
        route_id="imec_india_middle_east_europe",
        route_name="IMEC — India-Middle East-Europe Corridor",
        status="DEVELOPING",
        origin="Mumbai / JNPT (India)",
        destination="Piraeus / Hamburg (Europe)",
        distance_nm=6_200,                  # Combined sea legs; shorter vs Suez routing
        transit_days_summer=16.0,           # Estimated once rail links complete
        transit_days_winter=16.0,           # Year-round (land + sea multimodal)
        vs_suez_saving_nm=None,
        vs_suez_saving_days=6.0,            # Claimed 40% faster = ~6 days
        ice_class_required="None",
        current_annual_vessels=0,           # Rail/port infrastructure not yet built
        projected_2030_vessels=120,         # Projected container-equivalent units
        key_obstacle=(
            "Gaza conflict makes Israel hub diplomatically toxic for many nations; "
            "Saudi Arabia–Israel normalisation stalled; rail infrastructure gaps; "
            "Jordan and Saudi customs integration; UAE-Israel Abraham Accords fragility"
        ),
        key_enabler=(
            "G20 backing (US, India, EU, Saudi Arabia, UAE); India $350B trade ambition; "
            "Haifa port Chinese exit creates Western opportunity; "
            "India's EXIM bank and US DFC infrastructure financing"
        ),
        rate_premium_pct=12.0,
        co2_per_teu=0.016,                  # Rail section has lower CO2
        geopolitical_risk_score=0.72,       # Gaza, Saudi-Israel normalisation risk
        economic_viability_score=0.35,
    ),

    # 5 ── Trans-Caspian International Transport Route (TITR / Middle Corridor)
    # China → Kazakhstan (rail) → Caspian Sea (ferry) → Azerbaijan
    # → Georgia → Turkey → Europe.
    # Belt and Road alternative avoiding Russia post-2022 Ukraine invasion.
    # Volume tripled 2022-2023 as shippers sought Russia bypass.
    # Bottlenecks: Caspian ferry capacity, Kazakh/Azerbaijani rail gauge change.
    EmergingRoute(
        route_id="trans_caspian_titr",
        route_name="Trans-Caspian Route (TITR / Middle Corridor)",
        status="OPERATIONAL",
        origin="Xian / Chengdu (China)",
        destination="Istanbul / Vienna (Europe)",
        distance_nm=4_900,                  # Overland + Caspian ferry equivalent nm
        transit_days_summer=18.0,           # Rail + ferry combined
        transit_days_winter=21.0,           # Winter slowdowns at Caspian ports
        vs_suez_saving_nm=None,
        vs_suez_saving_days=None,
        ice_class_required="None",
        current_annual_vessels=850,         # Container-equivalent trains per year
        projected_2030_vessels=3_500,
        key_obstacle=(
            "Caspian ferry capacity severely bottlenecked (Aktau, Alat ports); "
            "rail gauge change at Baku; Turkish customs delays; "
            "competing gauge standards; Caspian storms disrupt ferry schedules"
        ),
        key_enabler=(
            "Post-2022 Russia bypass imperative; EU Global Gateway investment; "
            "Azerbaijan-Georgia-Turkey infrastructure upgrade; "
            "China's Central Asia rail programme; Kazakhstan-EU trade agreements"
        ),
        rate_premium_pct=22.0,              # Rail surcharges + multimodal complexity
        co2_per_teu=0.012,                  # Rail lower than sea per km
        geopolitical_risk_score=0.41,       # Armenia-Azerbaijan risk; Georgia stability
        economic_viability_score=0.55,
    ),

    # 6 ── Cape of Good Hope (Suez Bypass) ──────────────────────────────────
    # Revived massively from December 2023 due to Houthi drone/missile attacks
    # in the Red Sea. 60%+ of Asia-Europe traffic rerouted here by mid-2024.
    # +3,500 nm and 7-10 days vs Suez routing. Zero geopolitical risk.
    # Standard route for vessels below neo-panamax; used by 19th-century trade.
    EmergingRoute(
        route_id="cape_of_good_hope_bypass",
        route_name="Cape of Good Hope (Suez Bypass)",
        status="OPERATIONAL",
        origin="Shanghai / Singapore",
        destination="Rotterdam / Felixstowe",
        distance_nm=24_500,                 # Full Asia–Europe via Cape
        transit_days_summer=30.0,
        transit_days_winter=33.0,           # Cape storms add 2-3 days in winter
        vs_suez_saving_nm=None,             # This route is longer, not shorter
        vs_suez_saving_days=None,
        ice_class_required="None",
        current_annual_vessels=18_000,      # High: majority of Asia-Europe traffic
        projected_2030_vessels=8_000,       # Expected to normalize if Red Sea reopens
        key_obstacle=(
            "Adds 3,500 nm and 7-10 days vs Suez; +$400-800/FEU fuel surcharge; "
            "Cape Town congestion at anchorage; no operational reason except security; "
            "higher bunker consumption on longer route"
        ),
        key_enabler=(
            "Red Sea / Houthi crisis ongoing since Dec 2023; zero direct security risk; "
            "lower war risk insurance vs Red Sea transits (~1.5-2.5% of cargo value); "
            "carrier schedule reliability despite longer voyage"
        ),
        rate_premium_pct=18.0,              # Fuel + scheduling premium vs Suez normal
        co2_per_teu=0.024,                  # Higher CO2: longer distance + heavier fuel
        geopolitical_risk_score=0.04,       # Essentially zero
        economic_viability_score=0.88,      # Fully operational; only used due to crisis
    ),

    # 7 ── Neo-Panamax / Panama Canal Expansion Impact ─────────────────────
    # Neo-Panamax locks opened June 2016 allow vessels up to 14,000 TEU
    # (vs old 5,000 TEU Panamax limit). Competes with Suez for Asia-East US.
    # 2024 drought reduced water availability; draft restrictions cut utilisation.
    # Key corridor: Asia → US East/Gulf Coast; alternative to Suez + Transatlantic.
    EmergingRoute(
        route_id="neopanamax_canal",
        route_name="Neo-Panamax Canal Expansion Route",
        status="OPERATIONAL",
        origin="Yantian / Kaohsiung (Asia)",
        destination="Savannah / New York (US East)",
        distance_nm=13_200,                 # Asia → US East via Panama
        transit_days_summer=26.0,
        transit_days_winter=26.0,           # Year-round but water-constrained
        vs_suez_saving_nm=None,
        vs_suez_saving_days=None,
        ice_class_required="None",
        current_annual_vessels=5_200,       # Neo-Panamax transits per year
        projected_2030_vessels=6_800,
        key_obstacle=(
            "Gatun Lake water levels subject to El Nino drought (2024 draft cut to 13.4 m); "
            "booking queue during water restrictions; competing Suez+Transatlantic routing; "
            "canal authority toll increases (up 15-30% 2023-2024)"
        ),
        key_enabler=(
            "Only viable route for non-Suez Asia→US East trade; "
            "US nearshoring/friendshoring driving US East Coast import growth; "
            "neo-Panamax vessels 2.8x cargo capacity vs old Panamax; "
            "canal authority water management investment"
        ),
        rate_premium_pct=6.0,               # Canal tolls vs Suez equivalent
        co2_per_teu=0.018,
        geopolitical_risk_score=0.22,       # Climate/water risk; stable governance
        economic_viability_score=0.74,
    ),

    # 8 ── East Africa Trade Corridor ───────────────────────────────────────
    # Growing corridor as East Africa (Kenya, Tanzania, Ethiopia, Uganda)
    # industrialises and consumer markets expand.
    # Connection between Asia and rapidly growing African consumer market.
    # Mombasa, Dar es Salaam, Djibouti as key hubs. Standard Indian Ocean route.
    # +15% CAGR trade volume growth projected 2025-2030.
    EmergingRoute(
        route_id="east_africa_corridor",
        route_name="East Africa Trade Corridor",
        status="DEVELOPING",
        origin="Shanghai / Colombo (Asia)",
        destination="Mombasa / Dar es Salaam (East Africa)",
        distance_nm=6_800,
        transit_days_summer=16.0,
        transit_days_winter=18.0,           # Monsoon disruption (Jun–Sep)
        vs_suez_saving_nm=None,
        vs_suez_saving_days=None,
        ice_class_required="None",
        current_annual_vessels=1_400,
        projected_2030_vessels=3_200,
        key_obstacle=(
            "Port infrastructure gaps: Mombasa depth <14 m; berth availability; "
            "inland customs and logistics bottlenecks; Djibouti debt-trap concerns; "
            "currency convertibility issues (Kenya shilling, Tanzania shilling); "
            "piracy risk (Gulf of Aden, Somali coast)"
        ),
        key_enabler=(
            "Sub-Saharan Africa GDP growth 4-6% annually; "
            "AfCFTA (African Continental Free Trade Area) reducing intra-Africa tariffs; "
            "Lamu Port–South Sudan–Ethiopia Transport Corridor (LAPSSET); "
            "China-Africa BRI investment; India's competing Africa engagement"
        ),
        rate_premium_pct=14.0,              # Port inefficiency + lower volume premium
        co2_per_teu=0.017,
        geopolitical_risk_score=0.35,       # Stable but Somalia piracy, Ethiopia conflict
        economic_viability_score=0.62,
    ),
]

# Lookup by route_id
EMERGING_ROUTES_BY_ID: dict[str, EmergingRoute] = {r.route_id: r for r in EMERGING_ROUTES}


# ---------------------------------------------------------------------------
# Status colours (for UI use)
# ---------------------------------------------------------------------------

STATUS_COLORS: dict[str, str] = {
    "OPERATIONAL": "#10b981",    # Green
    "PILOT":       "#3b82f6",    # Blue
    "DEVELOPING":  "#f59e0b",    # Amber
    "FUTURE":      "#8b5cf6",    # Purple
}


# ---------------------------------------------------------------------------
# Viability calculator
# ---------------------------------------------------------------------------

def compute_route_viability(emerging_route: EmergingRoute, freight_rate: float) -> dict:
    """Compute economic viability metrics for an emerging route vs its traditional alternative.

    For Arctic routes (NSR, NWP, Transpolar), the traditional alternative is the
    Suez/Malacca route. For others, compares against the relevant incumbent.

    Parameters
    ----------
    emerging_route : EmergingRoute
        The route to analyse.
    freight_rate : float
        Current market freight rate in USD/FEU (e.g. SCFI spot for Asia–Europe).

    Returns
    -------
    dict with keys:
        route_id, route_name, current_rate_usd, break_even_rate_usd,
        rate_premium_absolute, distance_saving_pct, time_saving_days,
        is_competitive_now, arctic_escort_cost_per_feu,
        co2_penalty_vs_traditional, geo_risk_premium_usd,
        net_advantage_usd, recommendation, notes
    """
    logger.debug(
        "compute_route_viability: route={} freight_rate={:.0f}",
        emerging_route.route_id,
        freight_rate,
    )

    rid = emerging_route.route_id

    # ── Base premium cost ────────────────────────────────────────────────────
    rate_premium_absolute = freight_rate * (emerging_route.rate_premium_pct / 100.0)

    # ── Arctic-specific: icebreaker escort fee allocation per FEU ────────────
    # Russia charges ~$200,000–$400,000 per convoy escort.
    # Assume a 14,000 TEU vessel at 70% utilisation = 9,800 TEU = 4,900 FEU.
    # Midpoint escort fee $300k / 4,900 FEU = ~$61/FEU.
    arctic_escort_cost_per_feu = 0.0
    if rid in ("northern_sea_route", "northwest_passage", "transpolar_route"):
        escort_total = 300_000.0
        feu_per_vessel = 4_900.0
        arctic_escort_cost_per_feu = escort_total / feu_per_vessel

    # ── Distance saving benefit ──────────────────────────────────────────────
    # Traditional Asia–Europe Suez route: ~21,000 nm.
    # Traditional Asia–US East via Panama:~13,500 nm.
    # Standard bunker cost assumption: $550/tonne VLSFO, 180t/day consumption,
    # 14,000 TEU at 70% = 4,900 FEU.
    bunker_per_day = 550.0 * 180.0          # USD per day
    feu_count = 4_900.0
    bunker_per_day_per_feu = bunker_per_day / feu_count   # ~$20.20/FEU/day

    # Traditional reference distances
    if rid in ("northern_sea_route",):
        trad_nm = 21_000
    elif rid in ("northwest_passage",):
        trad_nm = 23_800                     # Via Panama, Asia → US East
    elif rid in ("transpolar_route",):
        trad_nm = 14_000                     # Via Panama
    elif rid in ("cape_of_good_hope_bypass",):
        trad_nm = 21_000                     # Suez is the traditional route
    elif rid in ("neopanamax_canal",):
        trad_nm = 16_000                     # Suez + Transatlantic alternative
    else:
        trad_nm = 0                          # No direct nautical-mile comparison

    vessel_speed_knots = 16.0               # Typical laden speed
    if trad_nm > 0:
        trad_days = trad_nm / (vessel_speed_knots * 24.0)
        em_days = emerging_route.distance_nm / (vessel_speed_knots * 24.0)
        day_saving = trad_days - em_days
        fuel_saving_usd_per_feu = day_saving * bunker_per_day_per_feu
        distance_saving_pct = ((trad_nm - emerging_route.distance_nm) / trad_nm) * 100.0
    else:
        day_saving = 0.0
        fuel_saving_usd_per_feu = 0.0
        distance_saving_pct = 0.0

    # ── CO2 penalty vs standard Suez route (0.020 kg/TEU/nm) ───────────────
    suez_co2_rate = 0.020   # kg CO2 per TEU per nm (Suez/Malacca standard vessel)
    if trad_nm > 0:
        co2_trad = suez_co2_rate * trad_nm
        co2_emerging = emerging_route.co2_per_teu * emerging_route.distance_nm
        co2_delta_kg = co2_emerging - co2_trad
        # Convert to rough cost: EU ETS ~ EUR 65/tonne = USD 70/tonne
        co2_penalty_usd_per_feu = max(0.0, co2_delta_kg / 1000.0 * 70.0 * 2.0)
        # x2 for 2 FEU per TEU conversion factor; simplified
    else:
        co2_delta_kg = 0.0
        co2_penalty_usd_per_feu = 0.0

    # ── Geopolitical risk premium ────────────────────────────────────────────
    # Model: 1.0 geo risk score = $500/FEU war-risk-insurance equivalent
    geo_risk_premium_usd = emerging_route.geopolitical_risk_score * 500.0

    # ── Net advantage (positive = emerging route is cheaper) ────────────────
    # Advantage = fuel_saving - rate_premium - escort_cost - co2_penalty - geo_risk
    net_advantage_usd = (
        fuel_saving_usd_per_feu
        - rate_premium_absolute
        - arctic_escort_cost_per_feu
        - co2_penalty_usd_per_feu
        - geo_risk_premium_usd
    )

    # ── Break-even rate ──────────────────────────────────────────────────────
    # At what freight rate does the emerging route cost = traditional route cost?
    # net_advantage = 0 when:
    # fuel_saving = pct * rate + escort + co2_penalty + geo_risk
    # rate * pct = fuel_saving - escort - co2_penalty - geo_risk
    # rate_breakeven = (fuel_saving - escort - co2 - geo) / (pct/100)
    pct_decimal = emerging_route.rate_premium_pct / 100.0
    if pct_decimal > 0:
        numerator = (
            fuel_saving_usd_per_feu
            - arctic_escort_cost_per_feu
            - co2_penalty_usd_per_feu
            - geo_risk_premium_usd
        )
        break_even_rate = numerator / pct_decimal if numerator > 0 else float("inf")
    else:
        break_even_rate = 0.0

    # ── Recommendations ─────────────────────────────────────────────────────
    if emerging_route.status == "FUTURE":
        recommendation = "Not actionable — theoretical route only; re-evaluate post-2035"
    elif emerging_route.economic_viability_score >= 0.75:
        recommendation = "Commercially viable now — can book without premium risk"
    elif net_advantage_usd >= 0:
        recommendation = "Competitive at current rates — monitor closely"
    elif break_even_rate != float("inf") and break_even_rate < freight_rate * 1.5:
        recommendation = (
            "Near break-even — viable if rates rise or geo-risk premium drops"
        )
    else:
        recommendation = "Not yet competitive at current rates — track Arctic season opening"

    # ── Status note ─────────────────────────────────────────────────────────
    notes_parts: list[str] = []
    if emerging_route.status == "OPERATIONAL":
        notes_parts.append("Route currently operational")
    elif emerging_route.status == "PILOT":
        notes_parts.append("Pilot/experimental — limited slot availability")
    elif emerging_route.status == "DEVELOPING":
        notes_parts.append("Infrastructure under construction; projected 2027-2030 full ops")
    else:
        notes_parts.append("Future route — no commercial bookings possible")

    if rid == "northern_sea_route":
        notes_parts.append(
            "Western carriers have substantially exited post-Ukraine sanctions (2022). "
            "Estimated 30+ major Western operators no longer transit."
        )
    if rid == "cape_of_good_hope_bypass":
        notes_parts.append(
            "Currently handling 60%+ of Asia-Europe traffic due to Houthi Red Sea attacks. "
            "Premium reflects fuel surcharge, not route novelty."
        )
    if rid == "imec_india_middle_east_europe":
        notes_parts.append(
            "Gaza conflict and stalled Saudi-Israel normalisation are major near-term blockers."
        )

    notes = ". ".join(notes_parts) + "." if notes_parts else ""

    result = {
        "route_id": emerging_route.route_id,
        "route_name": emerging_route.route_name,
        "status": emerging_route.status,
        "current_rate_usd": round(freight_rate, 2),
        "break_even_rate_usd": round(break_even_rate, 2) if break_even_rate != float("inf") else None,
        "rate_premium_absolute": round(rate_premium_absolute, 2),
        "distance_saving_pct": round(distance_saving_pct, 1),
        "time_saving_days": round(day_saving, 1),
        "is_competitive_now": net_advantage_usd >= 0,
        "arctic_escort_cost_per_feu": round(arctic_escort_cost_per_feu, 2),
        "co2_penalty_vs_traditional_usd": round(co2_penalty_usd_per_feu, 2),
        "geo_risk_premium_usd": round(geo_risk_premium_usd, 2),
        "fuel_saving_usd_per_feu": round(fuel_saving_usd_per_feu, 2),
        "net_advantage_usd": round(net_advantage_usd, 2),
        "economic_viability_score": emerging_route.economic_viability_score,
        "geopolitical_risk_score": emerging_route.geopolitical_risk_score,
        "recommendation": recommendation,
        "notes": notes,
    }

    logger.debug(
        "compute_route_viability: {} net_advantage={:.0f} break_even={} competitive={}",
        emerging_route.route_id,
        net_advantage_usd,
        result["break_even_rate_usd"],
        result["is_competitive_now"],
    )
    return result
