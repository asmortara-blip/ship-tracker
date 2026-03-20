"""
Weather Risk Model for Cargo Ship Container Tracker

Models climate and seasonal weather risks affecting global shipping routes.
Weather is the second-largest source of voyage delay after geopolitical events.

Key functions:
  compute_route_weather_risk(route_id)        -> WeatherRiskIndex
  get_current_season_alerts()                 -> list[WeatherRiskEvent]
  compute_weather_adjusted_eta(route_id, nominal_days) -> tuple[float, float]
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import List, Tuple

from loguru import logger


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class WeatherRiskEvent:
    event_name: str
    risk_type: str                    # "TYPHOON"|"HURRICANE"|"FOG"|"ICE"|"MONSOON"|"STORM"
    affected_routes: List[str]        # route_ids
    affected_ports: List[str]         # UN/LOCODE strings
    probability_pct: float            # 0-100, probability of disruption in-season
    delay_days_if_occurs: float       # expected delay days when event materialises
    season_months: List[int]          # 1=Jan … 12=Dec
    current_risk_level: str           # "ACTIVE"|"ELEVATED"|"SEASONAL"|"LOW"
    description: str
    mitigation: str


@dataclass
class WeatherRiskIndex:
    route_id: str
    current_risk_score: float                    # 0-1
    annualized_delay_days: float                 # expected additional days per year
    peak_risk_months: List[int]
    primary_risk_type: str
    historical_disruption_frequency_pct: float   # % of voyages disrupted historically


# ---------------------------------------------------------------------------
# Comprehensive weather risk event catalogue (18 events)
# ---------------------------------------------------------------------------

WEATHER_RISK_EVENTS: List[WeatherRiskEvent] = [

    # 1 — Western Pacific Typhoon Season
    WeatherRiskEvent(
        event_name="Western Pacific Typhoon Season",
        risk_type="TYPHOON",
        affected_routes=[
            "transpacific_eb",
            "transpacific_wb",
            "intra_asia_china_sea",
            "intra_asia_china_japan",
            "sea_transpacific_eb",
            "asia_europe",
            "ningbo_europe",
        ],
        affected_ports=["CNSHA", "CNSZN", "HKHKG", "KRPUS", "JPYOK", "CNNBO", "TWKHH"],
        probability_pct=68.0,
        delay_days_if_occurs=4.0,
        season_months=[6, 7, 8, 9, 10, 11],
        current_risk_level="SEASONAL",
        description=(
            "The Western Pacific typhoon season (June-November) generates on average 26 named "
            "tropical storms per year, of which 16 reach typhoon intensity. Super-typhoons with "
            "sustained winds above 150 mph can close ports for 3-7 days and force vessels to "
            "heave-to or take extreme evasive routing, adding 3-5 days per event. Shanghai, "
            "Shenzhen, Hong Kong, Busan, and Yokohama are most exposed. The South China Sea "
            "concentration period (August-October) poses the highest disruption risk for "
            "intra-Asia and trans-Pacific eastbound services."
        ),
        mitigation=(
            "Monitor JMA/JTWC advisories 5-7 days ahead. Reroute south of storm track where "
            "possible. Consider port omission/substitution to Busan or Yokohama if South China "
            "ports are at risk. Build 4-5 day weather buffers into schedules June-November."
        ),
    ),

    # 2 — Atlantic Hurricane Season (US East Coast)
    WeatherRiskEvent(
        event_name="Atlantic Hurricane Season",
        risk_type="HURRICANE",
        affected_routes=[
            "transatlantic",
            "us_east_south_america",
            "transpacific_eb",   # Panama Canal approaches
        ],
        affected_ports=["USNYC", "USSAV", "USMIA", "USJAX", "USBAL"],
        probability_pct=42.0,
        delay_days_if_occurs=3.0,
        season_months=[6, 7, 8, 9, 10, 11],
        current_risk_level="SEASONAL",
        description=(
            "The Atlantic hurricane season (June-November, peak August-October) affects US East "
            "Coast ports from Miami to New York. A Category 3+ hurricane making landfall near a "
            "major port typically triggers 2-5 days of pre-storm preparation/closure and 1-3 "
            "days of post-storm recovery. The Gulf of Mexico sees higher storm frequency, but "
            "East Coast landfalls cause the most container trade disruption. On average 2-3 "
            "significant storms threaten US East Coast ports per season."
        ),
        mitigation=(
            "Use NHC 5-day track forecasts to initiate port-omission decisions 72 hours in "
            "advance. Divert to Baltimore, Philadelphia, or Halifax as alternates. Pre-position "
            "vessels in safe anchorages. Build 3-day weather contingency into Transatlantic "
            "schedules August-October."
        ),
    ),

    # 3 — North Pacific Winter Storms (Trans-Pacific)
    WeatherRiskEvent(
        event_name="North Pacific Winter Storms",
        risk_type="STORM",
        affected_routes=[
            "transpacific_eb",
            "transpacific_wb",
            "sea_transpacific_eb",
        ],
        affected_ports=["USLAX", "USLGB", "USSEQ", "JPYOK", "CNSHA"],
        probability_pct=72.0,
        delay_days_if_occurs=1.5,
        season_months=[11, 12, 1, 2, 3],
        current_risk_level="ELEVATED",
        description=(
            "The North Pacific polar jet stream intensifies from November through March, "
            "generating powerful extra-tropical cyclones with wave heights of 10-14 metres on "
            "the great-circle trans-Pacific route. Modern post-panamax vessels must slow-steam "
            "or alter course, adding 1-2 days per crossing. The northern great-circle routing "
            "is most affected; vessels may deviate to lower latitudes, adding distance. Winter "
            "2025/26 is forecast to be active given developing La Nina conditions."
        ),
        mitigation=(
            "Use northern-route weather routing services (Weathernews, StormGeo, BV) to "
            "optimise between great-circle efficiency and seakeeping constraints. Allow 1-2 day "
            "ETA buffers November-March. Consider southern routing (+2 days distance) for "
            "cargo with high damage risk (e.g. heavy machinery)."
        ),
    ),

    # 4 — North-East Indian Ocean Monsoon (South Asia to Europe)
    WeatherRiskEvent(
        event_name="NE Indian Ocean Monsoon Season",
        risk_type="MONSOON",
        affected_routes=[
            "south_asia_to_europe",
            "middle_east_to_europe",
            "middle_east_to_asia",
            "asia_europe",
        ],
        affected_ports=["LKCMB", "INNSW", "PKKARR", "AEJEA", "SGSIN"],
        probability_pct=55.0,
        delay_days_if_occurs=2.0,
        season_months=[6, 7, 8, 9],
        current_risk_level="SEASONAL",
        description=(
            "The south-west monsoon (June-September) generates persistent swell of 3-5 metres "
            "across the Arabian Sea and Bay of Bengal, with occasional depressions intensifying "
            "to cyclonic storms. Port operations at Colombo and Indian West Coast ports are "
            "frequently disrupted. Vessel roll amplitudes increase significantly, raising "
            "container lashing stress and cargo damage rates. The Bay of Bengal sees the "
            "highest density of monsoon depressions that can strengthen to cyclones."
        ),
        mitigation=(
            "Pre-stow heavy and high-value containers on centerline positions. Apply tropical "
            "monsoon lashing patterns per BV/DNV guidelines. Build 2-day schedule buffers "
            "June-September. Arrange cargo insurance specific endorsements for monsoon-season "
            "voyages transiting the Arabian Sea."
        ),
    ),

    # 5 — Panama Canal Low Water (El Nino years)
    WeatherRiskEvent(
        event_name="Panama Canal Low Water / Draft Restrictions",
        risk_type="STORM",  # classified under STORM as drought-driven climate event
        affected_routes=[
            "transpacific_eb",
            "us_east_south_america",
        ],
        affected_ports=["PAPTY", "USSAV", "USNYC", "USLAX"],
        probability_pct=38.0,
        delay_days_if_occurs=5.0,
        season_months=[1, 2, 3, 10, 11, 12],
        current_risk_level="ELEVATED",
        description=(
            "El Nino events suppress rainfall over Panama's watershed, lowering Gatun Lake "
            "levels and forcing draft restrictions on neo-panamax transits. In 2023-24, the "
            "Canal Authority cut maximum draft from 50 to 44 feet, forcing vessels to sail "
            "partially loaded or await a slot. Queue times reached 18+ days. Current La Nina "
            "transition should ease restrictions but residual queue management adds 2-5 days "
            "to transit schedules. ENSO monitoring is essential for Panama-dependent routes."
        ),
        mitigation=(
            "Monitor Panama Canal Authority daily bulletins for draft restriction updates. "
            "Book neo-panamax slots 60-90 days ahead during El Nino years. Evaluate "
            "Suez/Cape of Good Hope re-routing economics. Split shipments across Suez and "
            "Panama to hedge. Maintain booking flexibility with multiple alliance partners."
        ),
    ),

    # 6 — North Sea Winter Storms (North Europe)
    WeatherRiskEvent(
        event_name="North Sea Winter Storm Season",
        risk_type="STORM",
        affected_routes=[
            "asia_europe",
            "ningbo_europe",
            "transatlantic",
            "south_asia_to_europe",
            "middle_east_to_europe",
        ],
        affected_ports=["NLRTM", "DEHAM", "GBFXT", "BEANR", "DKAAR"],
        probability_pct=60.0,
        delay_days_if_occurs=0.75,
        season_months=[10, 11, 12, 1, 2, 3],
        current_risk_level="ELEVATED",
        description=(
            "The North Sea and English Channel experience persistent low-pressure systems "
            "October-March with gale-force conditions (Beaufort 7-10) occurring on 30-40% "
            "of days. Rotterdam, Hamburg, and Felixstowe face berth and crane outages when "
            "wind gusts exceed 50 knots. English Channel transits add 6-12 hours due to "
            "reduced speed and visibility. Feeder vessel services to Scandinavian and Baltic "
            "ports are most affected. Annual disruption cost for North European range "
            "estimated at $200-400M."
        ),
        mitigation=(
            "Use ECMWF ensemble forecasts for 10-day North Sea outlooks. Plan layovers at "
            "Antwerp or Le Havre as alternates to Rotterdam or Hamburg during storm peaks. "
            "Build 0.5-1 day ETA buffers October-March. Liaise with port authorities on "
            "crane suspension wind thresholds ahead of arrival."
        ),
    ),

    # 7 — Black Sea Ice Risk
    WeatherRiskEvent(
        event_name="Black Sea Ice Risk",
        risk_type="ICE",
        affected_routes=[
            "transatlantic",
            "north_africa_to_europe",
        ],
        affected_ports=["UAODS", "ROBUC", "TRIST", "BGVAR"],
        probability_pct=20.0,
        delay_days_if_occurs=2.5,
        season_months=[1, 2, 3],
        current_risk_level="LOW",
        description=(
            "Severe winters bring sea ice formation in the shallow north-western Black Sea, "
            "particularly near the Danube Delta and Odessa. Icebreaker escort is required "
            "for vessel calls to Constanta, Varna, and Odessa in January-February in cold "
            "years. Climate change has reduced frequency but sub-zero anomalies (as in "
            "January 2024) can still produce 3-7 day delays. Container volumes through "
            "Black Sea ports are modest by global standards; impact on global trade is "
            "contained but material for regional supply chains."
        ),
        mitigation=(
            "Check ice class requirements for Black Sea port calls January-March. Engage "
            "local port agents for daily ice reports. Build icebreaker escort costs into "
            "voyage estimates. Delay non-time-sensitive calls until ice clears (typically "
            "mid-March)."
        ),
    ),

    # 8 — Fog Season Shanghai (CNSHA)
    WeatherRiskEvent(
        event_name="Fog Season - Shanghai / Yangtze Delta",
        risk_type="FOG",
        affected_routes=[
            "transpacific_eb",
            "transpacific_wb",
            "asia_europe",
            "ningbo_europe",
            "intra_asia_china_sea",
            "intra_asia_china_japan",
        ],
        affected_ports=["CNSHA", "CNNBO", "CNTAO"],
        probability_pct=45.0,
        delay_days_if_occurs=1.0,
        season_months=[11, 12, 1, 2, 3],
        current_risk_level="ELEVATED",
        description=(
            "Dense sea fog forms over the East China Sea and Yangtze estuary from November "
            "through March when warm maritime air meets cold continental air masses. Shanghai "
            "port frequently suspends vessel movements when visibility drops below 500 metres, "
            "causing anchorage queue build-up of 50-150 vessels. Fog closures average 10-15 "
            "days per season, each lasting 6-48 hours. The queuing effect means even vessels "
            "arriving after fog clears face 1-2 day berth waits. Ningbo also experiences "
            "fog-related disruptions but to a lesser degree."
        ),
        mitigation=(
            "Monitor CMA, CMAC, and CMA CGM local agent weather advisories for Yangtze Delta "
            "fog forecasts. Build 1-day ETA buffer for Shanghai winter calls. Arrange flexible "
            "trucking/rail pre-carriage to allow cargo release from Ningbo as Shanghai "
            "alternate. Align vessel rotation scheduling to minimise arrivals during peak "
            "fog-probability windows (early morning, November-February)."
        ),
    ),

    # 9 — Bering Sea / North Pacific Cyclones (Arctic Route)
    WeatherRiskEvent(
        event_name="Bering Sea Cyclones / Sub-Arctic Routing",
        risk_type="STORM",
        affected_routes=[
            "transpacific_eb",
            "transpacific_wb",
        ],
        affected_ports=["USANC", "RUPED", "JPYOK"],
        probability_pct=30.0,
        delay_days_if_occurs=2.0,
        season_months=[9, 10, 11, 12, 1],
        current_risk_level="SEASONAL",
        description=(
            "The Bering Sea generates some of the most intense extra-tropical cyclones on "
            "Earth (the 'Aleutian Low'). Vessels routing near the Aleutian arc for "
            "great-circle efficiency encounter wave heights of 12-18 metres in severe storms. "
            "As climate change opens Arctic summer routes, Bering Sea passage risks increase "
            "for vessels using Northern Sea Route variants. Winter Bering storms have sunk "
            "bulk carriers and caused cargo losses on container vessels. Sub-arctic routing "
            "requires careful weather-routing optimisation."
        ),
        mitigation=(
            "Avoid great-circle routing within 200nm of the Aleutian Islands October-January. "
            "Use southern-deviation routing during Bering lows. Ensure vessel dynamic stability "
            "calculations account for maximum wave heights. Ice class recommended for any "
            "Arctic-adjacent routing."
        ),
    ),

    # 10 — Mediterranean Scirocco / Tramontane
    WeatherRiskEvent(
        event_name="Mediterranean Scirocco and Tramontane Winds",
        risk_type="STORM",
        affected_routes=[
            "med_hub_to_asia",
            "north_africa_to_europe",
            "asia_europe",
            "south_asia_to_europe",
        ],
        affected_ports=["GRPIR", "MATNM", "ITNAP", "EGPSD", "ESALG"],
        probability_pct=35.0,
        delay_days_if_occurs=0.75,
        season_months=[3, 4, 5, 9, 10, 11],
        current_risk_level="SEASONAL",
        description=(
            "The Scirocco (hot Saharan wind across the Central Mediterranean) and Tramontane "
            "(cold northerly wind in the Western Mediterranean) generate short-period steep "
            "waves that are disproportionately disruptive to large container vessels compared "
            "to their significant wave height. Piraeus anchorage can become untenable in "
            "sustained Meltemi (summer Aegean northerly, 30-40 knots). Tanger Med operations "
            "are impacted by Levanter winds through the Strait of Gibraltar, causing berth "
            "delays of 6-24 hours. Spring and autumn see the highest Scirocco frequency."
        ),
        mitigation=(
            "Plan Piraeus calls outside Meltemi season (June-August) where possible for "
            "schedule-sensitive cargo. Use Agios Nikolaos or Limassol as eastern Mediterranean "
            "alternates. Monitor Spanish Meteorological Agency (AEMET) for Gibraltar Strait "
            "wind forecasts. Build 0.5-1 day buffers for spring/autumn Mediterranean transits."
        ),
    ),

    # 11 — Red Sea Summer Heat (Cargo Damage Risk)
    WeatherRiskEvent(
        event_name="Red Sea Summer Heat / Cargo Damage Risk",
        risk_type="STORM",
        affected_routes=[
            "asia_europe",
            "ningbo_europe",
            "south_asia_to_europe",
            "middle_east_to_europe",
        ],
        affected_ports=["EGPSD", "DJJIB", "AEJEA", "SAJEN"],
        probability_pct=55.0,
        delay_days_if_occurs=0.25,
        season_months=[5, 6, 7, 8, 9],
        current_risk_level="SEASONAL",
        description=(
            "The Red Sea and Gulf of Aden experience extreme heat May-September with air "
            "temperatures reaching 45-50 degrees Celsius and sea surface temperatures "
            "exceeding 35 degrees. Container deck temperatures can reach 60-70 degrees, "
            "causing cargo damage to temperature-sensitive goods (food, pharmaceuticals, "
            "chemicals, electronics). Reefer power demand spikes significantly. Extreme "
            "heat also degrades mooring lines and container rubber seals. Dust storms "
            "(haboob) reduce visibility to near zero and can last 6-12 hours."
        ),
        mitigation=(
            "Ship temperature-sensitive cargo under reefer during summer Red Sea transits. "
            "Apply DCC (Damage to Contents of Containers) insurance endorsements. Use "
            "reflective dunnage on deck cargo. Time transits to minimize midday heat exposure "
            "where controllable. Ensure reefer plug capacity is pre-booked well in advance "
            "as demand peaks in summer."
        ),
    ),

    # 12 — South-West Indian Ocean Cyclone Season
    WeatherRiskEvent(
        event_name="South-West Indian Ocean Cyclone Season",
        risk_type="TYPHOON",
        affected_routes=[
            "asia_europe",
            "ningbo_europe",
            "china_south_america",
            "south_asia_to_europe",
        ],
        affected_ports=["ZAPLZ", "ZADUR", "MUPLU", "MGTNR"],
        probability_pct=25.0,
        delay_days_if_occurs=3.0,
        season_months=[11, 12, 1, 2, 3, 4],
        current_risk_level="SEASONAL",
        description=(
            "The South-West Indian Ocean cyclone season (November-April, peak January-March) "
            "generates 9-10 named systems per year. Vessels routing via the Cape of Good Hope "
            "from Asia to Europe (rerouted from Suez due to Red Sea tensions) now frequently "
            "encounter these systems. Port Elizabeth and Durban in South Africa are occasionally "
            "disrupted. La Reunion and Madagascar see the most intense landfalls. Cape of Good "
            "Hope cape rollers (3-6 metre swell from Southern Ocean) add persistent slow-steaming "
            "pressure independent of discrete cyclone events."
        ),
        mitigation=(
            "Monitor CMRS (Reunion) tropical cyclone advisories for vessels transiting via "
            "Cape of Good Hope. Build 3-4 day buffers into Cape-routed Asia-Europe schedules "
            "during the austral summer. Plan Durban/Port Elizabeth calls outside January-March "
            "peak season where possible. Vessels should be certified for Southern Ocean service."
        ),
    ),

    # 13 — Arctic Sea Ice / Northern Sea Route Seasonal Risk
    WeatherRiskEvent(
        event_name="Arctic Sea Ice and NSR Seasonal Risk",
        risk_type="ICE",
        affected_routes=[
            "transpacific_eb",
            "asia_europe",
        ],
        affected_ports=["RUPED", "NOLKG", "USANC"],
        probability_pct=15.0,
        delay_days_if_occurs=5.0,
        season_months=[1, 2, 3, 4, 10, 11, 12],
        current_risk_level="LOW",
        description=(
            "While Arctic sea ice extent is declining, the Northern Sea Route (NSR) remains "
            "navigable only July-October without nuclear icebreaker escort. Outside this window "
            "multi-year ice and polar lows create severe risk. The NSR offers a 40% distance "
            "saving (Asia-Europe) vs Suez but requires ice class vessels, Russian permits "
            "(currently withheld from most Western carriers), and icebreaker hire. Unexpected "
            "re-freeze events can trap convoys. Climate change is extending the season but "
            "interannual variability remains high."
        ),
        mitigation=(
            "Restrict NSR use to certified ice-class vessels (Arc4 minimum) with valid Russian "
            "FSB permits. Only operate July-September without nuclear icebreaker escort. "
            "Maintain satellite ice-chart subscriptions (NSIDC, AARI). Ensure P&I cover "
            "explicitly includes Arctic navigation. Carry emergency provisions for minimum "
            "30-day self-sufficiency."
        ),
    ),

    # 14 — South-East Asian Squall Season
    WeatherRiskEvent(
        event_name="South-East Asian Squall Lines / Sumatran Squalls",
        risk_type="STORM",
        affected_routes=[
            "intra_asia_china_sea",
            "sea_transpacific_eb",
            "middle_east_to_asia",
        ],
        affected_ports=["SGSIN", "MYPEN", "IDPLM", "PHMNL"],
        probability_pct=50.0,
        delay_days_if_occurs=0.25,
        season_months=[3, 4, 5, 10, 11],
        current_risk_level="SEASONAL",
        description=(
            "Sumatran squalls are fast-moving convective systems that develop over Sumatra and "
            "track north-east across the Strait of Malacca and Singapore Strait. They bring "
            "wind gusts of 50-70 knots with minimal warning (30-60 minutes) and can capsise "
            "lightly loaded vessels. Singapore port operations are suspended for 2-6 hour "
            "periods during severe squalls, creating berth queue build-up. Inter-tropical "
            "convergence zone (ITCZ) passage generates persistent squall lines across the "
            "South China Sea March-May and October-November."
        ),
        mitigation=(
            "Monitor MSS (Meteorological Service Singapore) 1-hour severe weather warnings. "
            "Ensure all deck containers are fully lashed before entering the Malacca Strait. "
            "Maintain adequate sea-room in the Singapore Strait for squall manoeuvring. "
            "Build 6-hour schedule buffers for Malacca Strait transits in squall season."
        ),
    ),

    # 15 — Australian North-West Shelf Cyclone Season
    WeatherRiskEvent(
        event_name="Australian North-West Shelf Cyclone Season",
        risk_type="TYPHOON",
        affected_routes=[
            "china_south_america",
            "sea_transpacific_eb",
        ],
        affected_ports=["AUBNE", "AUFRE", "AUMEL"],
        probability_pct=20.0,
        delay_days_if_occurs=2.0,
        season_months=[11, 12, 1, 2, 3, 4],
        current_risk_level="SEASONAL",
        description=(
            "The Australian cyclone season (November-April) produces 11 named systems per year "
            "with the Coral Sea, Gulf of Carpentaria, and North-West Shelf being most active "
            "zones. Fremantle, Brisbane, and Cairns are occasionally disrupted by tropical "
            "lows. Vessels routing south of Australia on China-South America great-circle "
            "courses encounter the roaring forties and furious fifties — persistent Southern "
            "Ocean westerlies with 5-8 metre swell. Port Melbourne and Fremantle see wave "
            "climate that occasionally prevents container vessel berthing."
        ),
        mitigation=(
            "Use Bureau of Meteorology (BOM) 7-day tropical cyclone outlooks for Australian "
            "coastal operations. Avoid Southern Ocean routing south of 45S October-April. "
            "Plan port calls to coincide with between-cyclone weather windows. Ensure Southern "
            "Ocean routing vessels carry adequate stability margins for 15-metre wave heights."
        ),
    ),

    # 16 — Bay of Bengal Cyclone Season
    WeatherRiskEvent(
        event_name="Bay of Bengal Cyclone Season",
        risk_type="TYPHOON",
        affected_routes=[
            "south_asia_to_europe",
            "middle_east_to_asia",
            "intra_asia_china_sea",
        ],
        affected_ports=["BDCGP", "INNSW", "MMRGN", "LKCMB"],
        probability_pct=30.0,
        delay_days_if_occurs=3.5,
        season_months=[4, 5, 10, 11],
        current_risk_level="SEASONAL",
        description=(
            "The Bay of Bengal produces intense cyclones in the pre-monsoon (April-May) and "
            "post-monsoon (October-November) windows. Bangladesh (Chittagong), Myanmar "
            "(Yangon), and India's Andhra/Odisha coasts are most vulnerable. Cyclone Mocha "
            "(May 2023) demonstrated how a Category 5 storm can paralyse Chittagong for "
            "5-7 days. Sri Lanka's Colombo transshipment hub lies on the storm track for "
            "eastward-tracking Bay cyclones. Post-monsoon Bay cyclones are intensifying "
            "due to record-warm Indian Ocean surface temperatures."
        ),
        mitigation=(
            "Monitor IMD and JTWC advisories for Bay of Bengal systems April-May and "
            "October-November. Arrange cargo prioritisation protocols for Chittagong-destined "
            "shipments during cyclone alerts. Assess Colombo transshipment hub contingency "
            "routing via Singapore for Bay cyclone events. Build 3-4 day buffers into "
            "schedules during the active cyclone windows."
        ),
    ),

    # 17 — Trans-Atlantic Winter Low Pressure Systems
    WeatherRiskEvent(
        event_name="Trans-Atlantic Winter Low Pressure Systems",
        risk_type="STORM",
        affected_routes=[
            "transatlantic",
            "europe_south_america",
            "north_africa_to_europe",
        ],
        affected_ports=["USNYC", "USBAL", "CAMON", "GBSOU", "GBFXT"],
        probability_pct=55.0,
        delay_days_if_occurs=1.0,
        season_months=[12, 1, 2, 3],
        current_risk_level="ELEVATED",
        description=(
            "North Atlantic winter lows tracking from Newfoundland to Iceland bring gale-force "
            "conditions across the central Atlantic, with significant wave heights of 8-12 "
            "metres on weekly average passages. US East Coast ports experience nor'easters "
            "January-March that close berths and disrupt container lifts for 1-2 days per "
            "event (average 3-5 events per winter). Halifax and Montreal are ice-affected in "
            "severe winters. The St Lawrence Seaway closes November-March, diverting seasonal "
            "cargo to Halifax."
        ),
        mitigation=(
            "Use NOAA Marine forecasts and Environment Canada for 7-day North Atlantic outlooks. "
            "Allow 1-day ETA buffers for trans-Atlantic crossings December-March. Pre-plan "
            "nor'easter contingency anchorages for US East Coast calls. Arrange shore-power "
            "for reefer cargo during extended anchorage waits."
        ),
    ),

    # 18 — South American Pacific Coastal Northers
    WeatherRiskEvent(
        event_name="South Pacific Coastal Wind Season (Paita / Callao)",
        risk_type="STORM",
        affected_routes=[
            "china_south_america",
            "transpacific_wb",
        ],
        affected_ports=["PECLL", "CLVAP", "CLSAI"],
        probability_pct=28.0,
        delay_days_if_occurs=1.5,
        season_months=[6, 7, 8, 9],
        current_risk_level="SEASONAL",
        description=(
            "The Humboldt Current upwelling intensifies Southern Hemisphere winter winds "
            "along the Peru and Chilean coasts (June-September). Sustained south-south-westerly "
            "gales of 30-45 knots build steep swells in an opposing direction to vessel tracks "
            "northbound from Chile to Peru. Callao (Lima) outer anchorage becomes untenable "
            "in severe events, forcing vessels to wait offshore. Container vessel roll periods "
            "in beam-sea conditions add cargo shifting risk. El Nino years reverse the pattern, "
            "bringing calmer conditions to Peru but instability to Ecuador and Colombia."
        ),
        mitigation=(
            "Schedule South American Pacific calls outside peak austral-winter period where "
            "possible. Request protected berth assignments at Callao. Ensure deck cargo lashing "
            "is inspected at Panama Canal or Colón before south-bound Pacific Coast legs. "
            "Monitor SENAMHI (Peru) and DMC (Chile) marine weather bulletins."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Route-to-event mapping helpers
# ---------------------------------------------------------------------------

def _get_events_for_route(route_id: str) -> List[WeatherRiskEvent]:
    """Return all WeatherRiskEvents that affect *route_id*."""
    return [ev for ev in WEATHER_RISK_EVENTS if route_id in ev.affected_routes]


# ---------------------------------------------------------------------------
# Risk level score mapping (analogous to geopolitical_monitor)
# ---------------------------------------------------------------------------

_RISK_LEVEL_SCORE: dict[str, float] = {
    "ACTIVE":    1.00,
    "ELEVATED":  0.70,
    "SEASONAL":  0.40,
    "LOW":       0.10,
}

_RISK_TYPE_SEVERITY: dict[str, float] = {
    "TYPHOON":   1.00,
    "HURRICANE": 0.95,
    "MONSOON":   0.65,
    "STORM":     0.60,
    "FOG":       0.40,
    "ICE":       0.45,
}


def _current_month() -> int:
    return datetime.date.today().month


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_route_weather_risk(route_id: str) -> WeatherRiskIndex:
    """Aggregate all WeatherRiskEvents affecting *route_id* into a WeatherRiskIndex.

    The composite risk score (0-1) is computed as:
        score = mean over all events of (
            probability_pct/100
            * risk_level_score
            * risk_type_severity
        )
    capped at 1.0.

    Annualised delay days = sum of (probability * delay_if_occurs * in_season_months/12).

    Parameters
    ----------
    route_id:
        One of the 17 canonical route IDs from route_registry.

    Returns
    -------
    WeatherRiskIndex populated with composite metrics.
    """
    events = _get_events_for_route(route_id)
    if not events:
        logger.debug("compute_route_weather_risk({}): no events -> zero index", route_id)
        return WeatherRiskIndex(
            route_id=route_id,
            current_risk_score=0.0,
            annualized_delay_days=0.0,
            peak_risk_months=[],
            primary_risk_type="NONE",
            historical_disruption_frequency_pct=0.0,
        )

    # Composite risk score
    score_accum = 0.0
    annualized_days = 0.0

    # Month-level risk accumulator for peak detection
    month_risk: dict[int, float] = {m: 0.0 for m in range(1, 13)}

    for ev in events:
        prob = ev.probability_pct / 100.0
        lvl  = _RISK_LEVEL_SCORE.get(ev.current_risk_level, 0.10)
        sev  = _RISK_TYPE_SEVERITY.get(ev.risk_type, 0.50)
        contribution = prob * lvl * sev
        score_accum += contribution

        # Annualised delay: probability * delay * (months active / 12)
        in_season_fraction = len(ev.season_months) / 12.0
        annualized_days += prob * ev.delay_days_if_occurs * in_season_fraction

        # Spread contribution across season months for peak detection
        per_month = contribution / max(len(ev.season_months), 1)
        for m in ev.season_months:
            month_risk[m] += per_month

    n = len(events)
    raw_score = score_accum / n
    clamped_score = min(1.0, raw_score)

    # Peak risk months: top-3 by accumulated monthly risk
    sorted_months = sorted(month_risk.items(), key=lambda x: x[1], reverse=True)
    peak_months = [m for m, _ in sorted_months[:3] if sorted_months[0][1] > 0]

    # Primary risk type: most common across events
    from collections import Counter
    type_counts = Counter(ev.risk_type for ev in events)
    primary_type = type_counts.most_common(1)[0][0]

    # Historical disruption frequency: average probability across events
    hist_freq = round(sum(ev.probability_pct for ev in events) / n, 1)

    idx = WeatherRiskIndex(
        route_id=route_id,
        current_risk_score=round(clamped_score, 4),
        annualized_delay_days=round(annualized_days, 2),
        peak_risk_months=sorted(peak_months),
        primary_risk_type=primary_type,
        historical_disruption_frequency_pct=hist_freq,
    )
    logger.debug(
        "compute_route_weather_risk({}): score={:.3f}, ann_delay={:.2f}d",
        route_id, idx.current_risk_score, idx.annualized_delay_days,
    )
    return idx


def get_current_season_alerts() -> List[WeatherRiskEvent]:
    """Return WeatherRiskEvents active in the current calendar month and not LOW risk.

    An event is 'in season' when the current month falls in its season_months list
    and current_risk_level is not 'LOW'.

    Returns
    -------
    List of WeatherRiskEvent sorted by risk level severity descending.
    """
    month = _current_month()
    order = {"ACTIVE": 0, "ELEVATED": 1, "SEASONAL": 2, "LOW": 3}
    alerts = [
        ev for ev in WEATHER_RISK_EVENTS
        if month in ev.season_months and ev.current_risk_level != "LOW"
    ]
    alerts.sort(key=lambda e: (order.get(e.current_risk_level, 9), -e.probability_pct))
    logger.debug(
        "get_current_season_alerts(): month={}, {} alerts found", month, len(alerts)
    )
    return alerts


def compute_weather_adjusted_eta(
    route_id: str,
    nominal_days: float,
) -> Tuple[float, float]:
    """Return (expected_days, worst_case_days) with weather buffer added.

    Expected days adds the expected value of weather delay:
        E[delay] = sum over in-season events of (probability * delay_if_occurs * in_season)

    Worst-case days adds the 90th-percentile scenario:
        Worst case = nominal + sum of (delay_if_occurs) for top-3 highest-impact in-season events

    Parameters
    ----------
    route_id:
        Canonical route ID.
    nominal_days:
        The standard/contractual transit time without weather buffer.

    Returns
    -------
    Tuple of (expected_days, worst_case_days), both rounded to 1 decimal.
    """
    month = _current_month()
    events = _get_events_for_route(route_id)

    # Filter to in-season events
    in_season = [ev for ev in events if month in ev.season_months]

    if not in_season:
        logger.debug(
            "compute_weather_adjusted_eta({}): no in-season events, returning nominal {:.1f}",
            route_id, nominal_days,
        )
        return (round(nominal_days, 1), round(nominal_days * 1.15, 1))

    # Expected delay: probability-weighted sum for in-season events
    expected_delay = sum(
        (ev.probability_pct / 100.0) * ev.delay_days_if_occurs
        for ev in in_season
    )

    # Worst-case: sum of delay_if_occurs for top-3 by delay magnitude
    sorted_by_impact = sorted(in_season, key=lambda e: e.delay_days_if_occurs, reverse=True)
    worst_case_delay = sum(ev.delay_days_if_occurs for ev in sorted_by_impact[:3])

    expected_days = round(nominal_days + expected_delay, 1)
    worst_case_days = round(nominal_days + worst_case_delay, 1)

    logger.debug(
        "compute_weather_adjusted_eta({}): nominal={:.1f}, exp_delay={:.2f}, "
        "wc_delay={:.2f} -> ({:.1f}, {:.1f})",
        route_id, nominal_days, expected_delay, worst_case_delay,
        expected_days, worst_case_days,
    )
    return (expected_days, worst_case_days)


# ---------------------------------------------------------------------------
# Convenience: all-route summary (used by the UI tab)
# ---------------------------------------------------------------------------

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

ROUTE_DISPLAY_NAMES: dict[str, str] = {
    "transpacific_eb":        "Trans-Pacific Eastbound",
    "transpacific_wb":        "Trans-Pacific Westbound",
    "asia_europe":            "Asia-Europe (Suez)",
    "ningbo_europe":          "Ningbo-Europe",
    "transatlantic":          "Transatlantic",
    "sea_transpacific_eb":    "SE Asia-Trans-Pacific EB",
    "middle_east_to_europe":  "Middle East-Europe",
    "middle_east_to_asia":    "Middle East-Asia",
    "south_asia_to_europe":   "South Asia-Europe",
    "intra_asia_china_sea":   "Intra-Asia: China-SE Asia",
    "intra_asia_china_japan": "Intra-Asia: China-Japan/Korea",
    "china_south_america":    "China-South America",
    "europe_south_america":   "Europe-South America",
    "med_hub_to_asia":        "Mediterranean Hub-Asia",
    "north_africa_to_europe": "North Africa-Europe",
    "us_east_south_america":  "US East-South America",
    "longbeach_to_asia":      "Long Beach-Asia",
}

# Nominal transit days per route (from route_registry)
_NOMINAL_TRANSIT_DAYS: dict[str, int] = {
    "transpacific_eb":        14,
    "transpacific_wb":        16,
    "asia_europe":            25,
    "ningbo_europe":          28,
    "transatlantic":          12,
    "sea_transpacific_eb":    14,
    "middle_east_to_europe":  22,
    "middle_east_to_asia":    10,
    "south_asia_to_europe":   20,
    "intra_asia_china_sea":    5,
    "intra_asia_china_japan":  3,
    "china_south_america":    35,
    "europe_south_america":   22,
    "med_hub_to_asia":        22,
    "north_africa_to_europe":  8,
    "us_east_south_america":  12,
    "longbeach_to_asia":      16,
}


def get_nominal_transit_days(route_id: str) -> int:
    """Return the nominal (no-weather) transit days for a route."""
    return _NOMINAL_TRANSIT_DAYS.get(route_id, 20)
