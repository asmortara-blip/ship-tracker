"""sanctions_tracker.py — Sanctions compliance tracker for cargo shipping.

Tracks active sanctions regimes affecting maritime trade (2025-2026),
dark fleet activity, and computes per-route compliance risk scores.

Sanctions violations can result in penalties up to $1 billion — this
module provides the structured data and risk-scoring logic required for
compliance due diligence on every voyage.

Key exports:
  ACTIVE_SANCTIONS       — list[SanctionsRegime]
  DARK_FLEET_2025        — DarkFleetTracker
  compute_route_compliance_risk(route_id) -> dict
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List

from loguru import logger


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SanctionsRegime:
    regime_name: str
    sanctioning_authority: str           # "OFAC" | "EU" | "UN" | "UK"
    target_country: str
    effective_date: date
    scope: str                           # Narrative scope description
    shipping_impact: str                 # "PROHIBITED" | "RESTRICTED" | "MONITORED"
    affected_routes: List[str]           # route IDs
    affected_ports: List[str]            # port LOCODEs or names
    penalty_per_violation_usd: int       # Maximum USD fine per violation
    compliance_cost_per_voyage_usd: int  # Estimated due-diligence cost per voyage
    risk_level: str                      # "CRITICAL" | "HIGH" | "MODERATE" | "LOW"
    workarounds: List[str]               # Legal alternatives
    enforcement_cases_2024: int          # Number of public enforcement actions in 2024


@dataclass
class DarkFleetTracker:
    estimated_vessels: int
    primary_sanctioned_country: str
    operating_regions: List[str]
    flag_states: List[str]
    insurance_status: str
    detection_risk: str          # "HIGH" | "MODERATE" | "LOW"
    market_impact: str           # Narrative description


# ---------------------------------------------------------------------------
# Active Sanctions Regimes — 2025 / 2026
# ---------------------------------------------------------------------------

ACTIVE_SANCTIONS: List[SanctionsRegime] = [

    SanctionsRegime(
        regime_name="Russia Oil Price Cap — G7/OFAC/EU",
        sanctioning_authority="OFAC",
        target_country="Russia",
        effective_date=date(2022, 12, 5),
        scope=(
            "G7, EU, and Australia-enforced price cap of $60/barrel on seaborne Russian crude oil. "
            "Western shipping services, insurance (P&I), and finance prohibited for above-cap cargoes. "
            "Covers crude, diesel, fuel oil, and other petroleum products (product cap: $45-100/bbl "
            "depending on category). Attestation chains required for all Russian-origin oil cargoes."
        ),
        shipping_impact="PROHIBITED",
        affected_routes=[
            "transatlantic",
            "asia_europe",
            "middle_east_to_europe",
            "europe_south_america",
            "north_africa_to_europe",
        ],
        affected_ports=[
            "RUUST",  # Ust-Luga
            "RUPRI",  # Primorsk
            "RUNVS",  # Novorossiysk
            "RUKGD",  # Kaliningrad
        ],
        penalty_per_violation_usd=1_000_000,
        compliance_cost_per_voyage_usd=8_500,
        risk_level="CRITICAL",
        workarounds=[
            "Only carry Russian oil cargoes verified at or below $60/bbl price cap",
            "Obtain full price attestation documentation chain from seller through broker",
            "Engage pre-approved compliance counsel for any Russian-origin cargo",
            "Avoid vessel calls at designated Russian export terminals",
        ],
        enforcement_cases_2024=47,
    ),

    SanctionsRegime(
        regime_name="Iran Comprehensive Sanctions — OFAC",
        sanctioning_authority="OFAC",
        target_country="Iran",
        effective_date=date(2012, 1, 23),
        scope=(
            "Near-total prohibition on US persons, US-flag vessels, and vessels owned or "
            "operated by US entities engaging in trade with Iran. EU maintains corresponding "
            "restrictions. Covers oil, petrochemicals, metals, and general cargo. Designation "
            "applies to Iranian shipping lines (IRISL) and National Iranian Tanker Company (NITC). "
            "Secondary sanctions risk applies to non-US entities transacting with designated parties."
        ),
        shipping_impact="PROHIBITED",
        affected_routes=[
            "middle_east_to_europe",
            "middle_east_to_asia",
            "asia_europe",
            "south_asia_to_europe",
        ],
        affected_ports=[
            "IRBND",  # Bandar Abbas
            "IRIMA",  # Imam Khomeini
            "IRBUZ",  # Bushehr
            "IRASA",  # Assaluyeh
        ],
        penalty_per_violation_usd=1_000_000,
        compliance_cost_per_voyage_usd=12_000,
        risk_level="CRITICAL",
        workarounds=[
            "Strict vessel vetting — verify no Iranian ownership layers via beneficial owner check",
            "Use IHS Fairplay / Windward vessel screening before chartering",
            "Refuse all cargoes without clear, non-Iranian certificate of origin",
            "Third-party intermediary routes are HIGH risk and generally not a safe harbor",
        ],
        enforcement_cases_2024=31,
    ),

    SanctionsRegime(
        regime_name="North Korea Total Embargo — UN/OFAC",
        sanctioning_authority="UN",
        target_country="North Korea",
        effective_date=date(2006, 10, 14),
        scope=(
            "Comprehensive UN Security Council embargo on all trade with North Korea, "
            "expanded via resolutions 1718, 1874, 2094, 2270, 2321, 2375, 2397. "
            "Prohibits coal, iron, seafood, textiles, machinery exports. Arms embargo in force. "
            "Known DPRK evasion tactics: ship-to-ship (STS) transfers in international waters, "
            "AIS spoofing, vessel reflagging. UN Panel of Experts has documented 200+ STS events."
        ),
        shipping_impact="PROHIBITED",
        affected_routes=[
            "intra_asia_china_sea",
            "intra_asia_china_japan",
            "transpacific_eb",
            "sea_transpacific_eb",
        ],
        affected_ports=[
            "KPNAM",  # Nampo
            "KPWON",  # Wonsan
            "KPCHJ",  # Chongjin
            "KPRJN",  # Rajin (Rason SEZ)
        ],
        penalty_per_violation_usd=1_000_000,
        compliance_cost_per_voyage_usd=6_000,
        risk_level="CRITICAL",
        workarounds=[
            "Enable AIS dark-period detection via vessel monitoring tools (Windward, Pole Star)",
            "Check vessel history for port calls within 180-day lookback at DPRK ports",
            "Verify no STS events in East China Sea / Yellow Sea during prior voyages",
            "Refuse any cargo with ambiguous North China / Yellow Sea provenance",
        ],
        enforcement_cases_2024=18,
    ),

    SanctionsRegime(
        regime_name="Venezuela Oil Sector Sanctions — OFAC",
        sanctioning_authority="OFAC",
        target_country="Venezuela",
        effective_date=date(2019, 8, 5),
        scope=(
            "Executive Order 13884 blocking property of Venezuelan government. OFAC SDN designations "
            "cover PdVSA (state oil company) and affiliates. Sector sanctions on oil/gas industry. "
            "General License 41 (and renewals) provided temporary authorisation for certain energy "
            "transactions with Chevron. Humanitarian exemptions for food and medicine available via "
            "specific OFAC general licenses. Tanker designations frequently updated."
        ),
        shipping_impact="RESTRICTED",
        affected_routes=[
            "us_east_south_america",
            "europe_south_america",
            "china_south_america",
            "transatlantic",
        ],
        affected_ports=[
            "VEPBL",  # Puerto La Cruz
            "VEJOS",  # Jose terminal
            "VEMOR",  # Moron
            "VELAG",  # La Guaira
        ],
        penalty_per_violation_usd=500_000,
        compliance_cost_per_voyage_usd=4_500,
        risk_level="HIGH",
        workarounds=[
            "Apply for specific OFAC general license for authorised transactions",
            "Chevron-specific license GL41C — only applies to designated Chevron operations",
            "Humanitarian cargo (food, medicine) available under GL 43 with proper documentation",
            "Monitor OFAC SDN list weekly — PdVSA affiliates frequently re-designated",
        ],
        enforcement_cases_2024=12,
    ),

    SanctionsRegime(
        regime_name="Cuba Embargo — OFAC (CACR)",
        sanctioning_authority="OFAC",
        target_country="Cuba",
        effective_date=date(1963, 2, 7),
        scope=(
            "Cuban Assets Control Regulations (CACR) — comprehensive embargo on US persons "
            "and US-flag vessels trading with Cuba. Non-US vessels that trade with Cuba are "
            "barred from US ports for 180 days (Trading with the Enemy Act). "
            "Applies to vessel calls, cargo, and finance. Some OFAC licenses available for "
            "agricultural exports, telecommunications, and authorised travel services. "
            "Low international shipping impact — primarily affects US-flag carriers."
        ),
        shipping_impact="RESTRICTED",
        affected_routes=[
            "us_east_south_america",
            "transatlantic",
            "europe_south_america",
        ],
        affected_ports=[
            "CUHAV",  # Havana
            "CUSCU",  # Santiago de Cuba
            "CUMZN",  # Manzanillo
            "CUCIG",  # Cienfuegos
        ],
        penalty_per_violation_usd=65_000,
        compliance_cost_per_voyage_usd=1_200,
        risk_level="LOW",
        workarounds=[
            "Non-US-flag vessels may trade with Cuba but face 180-day US port bar",
            "Apply for OFAC specific license for authorised Cuba trade (agricultural/humanitarian)",
            "US-flag vessels: strict prohibition, no workaround available",
            "Ensure vessel has no Cuba port call within prior 180 days before US entry",
        ],
        enforcement_cases_2024=4,
    ),

    SanctionsRegime(
        regime_name="Myanmar Targeted Sanctions — OFAC/EU/UK",
        sanctioning_authority="OFAC",
        target_country="Myanmar",
        effective_date=date(2021, 2, 11),
        scope=(
            "Post-coup (Feb 2021) targeted sanctions on Myanmar military (Tatmadaw) leadership, "
            "state-owned enterprises (MEHL, MEC), and designated sectors. "
            "Burma/Myanmar Sanctions Regulations expanded to restrict jet fuel imports, "
            "gemstone/jade trade, and timber. General shipping not fully prohibited but "
            "military-linked cargo and designated entity dealings prohibited. "
            "EU and UK have parallel measures. Commercial cargo largely unaffected if "
            "routed away from designated entities."
        ),
        shipping_impact="RESTRICTED",
        affected_routes=[
            "asia_europe",
            "sea_transpacific_eb",
            "intra_asia_china_sea",
            "south_asia_to_europe",
        ],
        affected_ports=[
            "MMRGN",  # Yangon (Rangoon)
            "MMSGG",  # Sittwe
            "MMMWD",  # Mawlamyine
        ],
        penalty_per_violation_usd=250_000,
        compliance_cost_per_voyage_usd=2_800,
        risk_level="MODERATE",
        workarounds=[
            "Screen all Myanmar cargo counterparties against OFAC SDN / EU asset freeze lists",
            "Avoid military-owned or military-linked port terminals (Thilawa — partial military stake)",
            "No jet fuel or refined petroleum cargo destined for Myanmar military end-users",
            "Jade/gemstone and timber cargo: full prohibition regardless of entity",
        ],
        enforcement_cases_2024=6,
    ),

    SanctionsRegime(
        regime_name="Sudan Conflict-Zone Restrictions — OFAC/UN",
        sanctioning_authority="OFAC",
        target_country="Sudan",
        effective_date=date(2023, 4, 15),
        scope=(
            "Escalated since April 2023 conflict between SAF and RSF. "
            "OFAC Darfur/Sudan sanctions remain active — SDN designations on RSF leadership "
            "and key military figures. UN arms embargo applies. "
            "Commercial shipping not fully prohibited but conflict creates severe operational risk. "
            "Port Sudan (primary maritime gateway) operationally impaired by conflict. "
            "Special OFAC authorisations required for humanitarian cargo."
        ),
        shipping_impact="MONITORED",
        affected_routes=[
            "asia_europe",
            "middle_east_to_europe",
            "south_asia_to_europe",
        ],
        affected_ports=[
            "SDPZU",  # Port Sudan
        ],
        penalty_per_violation_usd=250_000,
        compliance_cost_per_voyage_usd=3_500,
        risk_level="MODERATE",
        workarounds=[
            "Humanitarian cargo requires OFAC general or specific license",
            "War risk insurance essential — H&M and P&I separately needed for Sudan calls",
            "Master's right of refusal — ensure crew safety clause in charter parties",
            "Avoid cargo destined for RSF-controlled areas",
        ],
        enforcement_cases_2024=3,
    ),

    SanctionsRegime(
        regime_name="Yemen/Houthi Designated Entity Restrictions — OFAC",
        sanctioning_authority="OFAC",
        target_country="Yemen",
        effective_date=date(2024, 1, 17),
        scope=(
            "Ansarallah (Houthis) redesignated as Specially Designated Global Terrorist (SDGT) "
            "in January 2024. Prohibits material support, including shipping services that benefit "
            "Houthi-controlled territory without OFAC authorization. Red Sea/Gulf of Aden "
            "operational risk from Houthi drone and missile attacks on commercial vessels "
            "creates both physical and sanctions compliance risk. "
            "Combined operational and sanctions exposure makes this zone extremely high risk."
        ),
        shipping_impact="MONITORED",
        affected_routes=[
            "asia_europe",
            "ningbo_europe",
            "middle_east_to_europe",
            "south_asia_to_europe",
        ],
        affected_ports=[
            "YEHOD",  # Hodeidah
            "YESLL",  # Saleef
            "YEADE",  # Aden
        ],
        penalty_per_violation_usd=500_000,
        compliance_cost_per_voyage_usd=5_500,
        risk_level="HIGH",
        workarounds=[
            "Reroute via Cape of Good Hope — adds 12-14 days and ~$700/FEU fuel cost",
            "Apply for BMP (Best Management Practices) compliance if transiting Red Sea",
            "Enroll in UKMTO vessel reporting scheme for Red Sea transits",
            "Do not call at Hodeidah or Saleef — Houthi-controlled, OFAC nexus risk",
        ],
        enforcement_cases_2024=8,
    ),

    SanctionsRegime(
        regime_name="Russia OFAC/EU Broad Measures — Non-Oil",
        sanctioning_authority="EU",
        target_country="Russia",
        effective_date=date(2022, 2, 25),
        scope=(
            "Broad EU and OFAC sanctions on Russia following February 2022 invasion of Ukraine. "
            "Covers: luxury goods, aviation equipment, dual-use technology, steel/iron, coal, "
            "advanced semiconductor exports to Russia. EU import bans on Russian steel, coal, "
            "cement, fertilizers. SWIFT banking restrictions on designated Russian banks. "
            "Western port bans on Russian-flag vessels in EU, UK, Canada, US ports. "
            "Separate from oil price cap regime — applies to general cargo trade."
        ),
        shipping_impact="PROHIBITED",
        affected_routes=[
            "transatlantic",
            "asia_europe",
            "north_africa_to_europe",
            "europe_south_america",
        ],
        affected_ports=[
            "RULED",  # St. Petersburg
            "RUKGD",  # Kaliningrad
            "RUVVO",  # Vladivostok
            "RUNAK",  # Nakhodka
        ],
        penalty_per_violation_usd=1_000_000,
        compliance_cost_per_voyage_usd=7_000,
        risk_level="CRITICAL",
        workarounds=[
            "Russian-flag vessels: prohibited from EU/UK/Canadian ports entirely",
            "Non-Russian vessels: screen all cargo for dual-use classification (CCL/EAR)",
            "SWIFT restrictions — use non-designated banks for any Russia-adjacent transactions",
            "Fertilizer trade: some exemptions under OFAC GL, verify current status before cargo",
        ],
        enforcement_cases_2024=63,
    ),

    SanctionsRegime(
        regime_name="China Dual-Use / Decoupling Watch — BIS/DoD",
        sanctioning_authority="OFAC",
        target_country="China",
        effective_date=date(2023, 10, 7),
        scope=(
            "Not full sanctions but an active and expanding US-China decoupling framework. "
            "Bureau of Industry and Security (BIS) expanded Entity List restricts advanced "
            "semiconductor, AI chip, and quantum computing exports to China. "
            "Section 232 steel/aluminum tariffs in force. ITAR controls on military items. "
            "Emerging: potential shipping restrictions on Chinese military-linked carriers (COSCO). "
            "Monitor closely — rapid escalation risk from Taiwan scenario or trade war broadening. "
            "Current status: MONITORED with CRITICAL escalation potential."
        ),
        shipping_impact="MONITORED",
        affected_routes=[
            "transpacific_eb",
            "transpacific_wb",
            "sea_transpacific_eb",
            "asia_europe",
            "longbeach_to_asia",
        ],
        affected_ports=[
            "CNSHA",  # Shanghai
            "CNNBO",  # Ningbo
            "CNSZX",  # Shenzhen
            "CNTXG",  # Tianjin
        ],
        penalty_per_violation_usd=500_000,
        compliance_cost_per_voyage_usd=3_000,
        risk_level="MODERATE",
        workarounds=[
            "Screen all cargo against BIS Entity List and CCL for dual-use classification",
            "Avoid COSCO Shipping-subsidiary vessels for sensitive technology cargo",
            "Diversify to Vietnam/Thailand/Malaysia origin where possible (China+1 strategy)",
            "Maintain enhanced end-user certificates for advanced electronics shipments",
        ],
        enforcement_cases_2024=22,
    ),
]

# Convenience lookup
SANCTIONS_BY_COUNTRY: dict[str, List[SanctionsRegime]] = {}
for _s in ACTIVE_SANCTIONS:
    SANCTIONS_BY_COUNTRY.setdefault(_s.target_country, []).append(_s)


# ---------------------------------------------------------------------------
# Dark Fleet Tracker
# ---------------------------------------------------------------------------

DARK_FLEET_2025: DarkFleetTracker = DarkFleetTracker(
    estimated_vessels=600,
    primary_sanctioned_country="Russia",
    operating_regions=[
        "Baltic Sea",
        "Black Sea",
        "Eastern Mediterranean",
        "Gulf of Oman",
        "Strait of Hormuz",
        "Malacca Strait",
        "Yellow Sea",
        "Gulf of Mexico (Venezuelan oil)",
    ],
    flag_states=[
        "Gabon",       # Easy-flag, minimal oversight
        "Palau",       # Popular reflag destination post-sanctions
        "Cameroon",    # No vetting of beneficial owners
        "Togo",        # Low-cost flag, no AIS monitoring enforcement
        "Comoros",     # Minimal maritime authority capacity
        "Belize",      # Open registry, limited oversight
        "Tanzania",    # Growing dark fleet flag usage 2024-2025
    ],
    insurance_status=(
        "No Western P&I Club coverage (International Group excluded per sanctions). "
        "Using Russian National Reinsurance Company (RNRC), Iranian government-backed "
        "mutual P&I, or operating completely uninsured. ISCO (Insurance Company of "
        "the Sea of Oman) used for some Iranian-linked vessels. "
        "Estimated 35% of dark fleet operating with no valid insurance whatsoever."
    ),
    detection_risk="MODERATE",
    market_impact=(
        "~600 dark fleet vessels effectively cap the enforcement impact of the G7 oil price "
        "cap. Russian Urals crude moving to China and India at discount (~$10-15/bbl below Brent) "
        "via shadow tanker fleet, bypassing Western service prohibitions entirely. "
        "Iranian dark fleet (~200 vessels) similarly sustains OPEC+ non-compliance. "
        "Venezuelan oil (~50 vessels) routed via Trinidad/Tobago transshipment and "
        "ship-to-ship (STS) transfers in international waters. "
        "Total market: ~100,000 DWT of global VLCC/Aframax capacity diverted to shadow trade. "
        "EU Cap enforcement estimated at 50% effectiveness given dark fleet scale. "
        "Shadow premium for dark fleet tanker service: +$2-4M per voyage vs. clean market, "
        "reflecting risk premium for seizure, insurance, and flag-state costs."
    ),
)


# ---------------------------------------------------------------------------
# Route compliance risk data
# ---------------------------------------------------------------------------

# Sanctions exposure per route: list of (country, impact, weight)
# weight 1.0 = direct exposure, 0.5 = indirect/transshipment risk
_ROUTE_SANCTIONS_EXPOSURE: dict[str, List[tuple]] = {
    "transpacific_eb": [
        ("China", "MONITORED", 0.5),
    ],
    "transpacific_wb": [
        ("China", "MONITORED", 0.5),
    ],
    "asia_europe": [
        ("Russia", "PROHIBITED", 1.0),
        ("Iran", "PROHIBITED", 0.4),
        ("Yemen", "MONITORED", 0.8),
        ("Myanmar", "RESTRICTED", 0.2),
        ("Sudan", "MONITORED", 0.3),
    ],
    "transatlantic": [
        ("Russia", "PROHIBITED", 0.7),
        ("Cuba", "RESTRICTED", 0.2),
    ],
    "sea_transpacific_eb": [
        ("China", "MONITORED", 0.3),
        ("North Korea", "PROHIBITED", 0.3),
        ("Myanmar", "RESTRICTED", 0.3),
    ],
    "ningbo_europe": [
        ("Russia", "PROHIBITED", 0.9),
        ("Iran", "PROHIBITED", 0.3),
        ("Yemen", "MONITORED", 0.8),
    ],
    "middle_east_to_europe": [
        ("Iran", "PROHIBITED", 0.9),
        ("Russia", "PROHIBITED", 0.6),
        ("Yemen", "MONITORED", 0.9),
        ("Sudan", "MONITORED", 0.4),
    ],
    "middle_east_to_asia": [
        ("Iran", "PROHIBITED", 0.8),
        ("Yemen", "MONITORED", 0.7),
    ],
    "south_asia_to_europe": [
        ("Russia", "PROHIBITED", 0.5),
        ("Iran", "PROHIBITED", 0.3),
        ("Yemen", "MONITORED", 0.8),
        ("Myanmar", "RESTRICTED", 0.2),
    ],
    "intra_asia_china_sea": [
        ("North Korea", "PROHIBITED", 0.5),
        ("China", "MONITORED", 0.4),
        ("Myanmar", "RESTRICTED", 0.3),
    ],
    "intra_asia_china_japan": [
        ("North Korea", "PROHIBITED", 0.4),
        ("China", "MONITORED", 0.3),
    ],
    "china_south_america": [
        ("Venezuela", "RESTRICTED", 0.5),
        ("China", "MONITORED", 0.3),
    ],
    "europe_south_america": [
        ("Russia", "PROHIBITED", 0.4),
        ("Venezuela", "RESTRICTED", 0.6),
        ("Cuba", "RESTRICTED", 0.3),
    ],
    "med_hub_to_asia": [
        ("Russia", "PROHIBITED", 0.5),
        ("Iran", "PROHIBITED", 0.4),
        ("Yemen", "MONITORED", 0.7),
    ],
    "north_africa_to_europe": [
        ("Russia", "PROHIBITED", 0.4),
        ("Sudan", "MONITORED", 0.3),
        ("Yemen", "MONITORED", 0.3),
    ],
    "us_east_south_america": [
        ("Venezuela", "RESTRICTED", 0.7),
        ("Cuba", "RESTRICTED", 0.5),
        ("Russia", "PROHIBITED", 0.2),
    ],
    "longbeach_to_asia": [
        ("China", "MONITORED", 0.4),
        ("North Korea", "PROHIBITED", 0.3),
    ],
}

# Impact weight multipliers
_IMPACT_WEIGHTS: dict[str, float] = {
    "PROHIBITED": 1.0,
    "RESTRICTED": 0.55,
    "MONITORED":  0.20,
}

# Risk level labels by score
_SCORE_TO_RISK: list[tuple] = [
    (0.75, "CRITICAL"),
    (0.50, "HIGH"),
    (0.25, "MODERATE"),
    (0.00, "LOW"),
]

# Due diligence requirements by risk level
_DUE_DILIGENCE: dict[str, dict] = {
    "CRITICAL": {
        "level": "Enhanced Due Diligence — DO NOT USE without legal clearance",
        "steps": [
            "Full beneficial ownership verification (UBO registry check)",
            "OFAC SDN / EU consolidated list screening — cargo, vessel, owner, charterer",
            "Price attestation documentation for energy cargoes",
            "Legal counsel sign-off required before fixture",
            "Compliance officer pre-approval mandatory",
            "War risk insurance review",
            "AIS/dark-period vessel history check (180-day lookback)",
        ],
        "documentation": [
            "Certificate of origin (verified, notarised)",
            "Price attestation letter (for oil cargoes)",
            "End-user certificate",
            "Sanctions compliance certificate from operator",
            "P&I Club confirmation of coverage",
            "Legal opinion letter",
        ],
    },
    "HIGH": {
        "level": "Enhanced Due Diligence — legal review recommended",
        "steps": [
            "SDN list screening — all counterparties",
            "Beneficial owner check (company registry)",
            "Cargo origin documentation review",
            "Compliance team notification",
            "AIS history check (90-day lookback)",
        ],
        "documentation": [
            "Certificate of origin",
            "End-user certificate (dual-use goods)",
            "Counterparty sanctions screening certificate",
            "P&I confirmation",
        ],
    },
    "MODERATE": {
        "level": "Standard Enhanced Diligence — compliance checklist required",
        "steps": [
            "SDN / EU / UN list screening",
            "Cargo description review for restricted goods",
            "Vessel flag state check",
        ],
        "documentation": [
            "Certificate of origin",
            "Standard shipping documents",
            "Internal compliance checklist sign-off",
        ],
    },
    "LOW": {
        "level": "Standard Diligence — routine compliance checks",
        "steps": [
            "Standard SDN list screening",
            "Routine flag state and vessel checks",
        ],
        "documentation": [
            "Standard shipping documents",
            "Internal compliance sign-off",
        ],
    },
}


def compute_route_compliance_risk(route_id: str) -> dict:
    """Compute compliance risk profile for a given route.

    Parameters
    ----------
    route_id : str
        A shipping route identifier (e.g. "asia_europe").

    Returns
    -------
    dict with keys:
        route_id            : str
        risk_score          : float  [0.0, 1.0]
        risk_level          : str    CRITICAL / HIGH / MODERATE / LOW
        primary_exposures   : list[dict]  — sanctions regime details per exposure
        compliance_cost_usd : int    — estimated due diligence cost per voyage
        due_diligence       : dict   — steps, documentation, level label
        recommendation      : str
    """
    logger.debug("Computing compliance risk for route: {}", route_id)

    exposures_raw = _ROUTE_SANCTIONS_EXPOSURE.get(route_id, [])

    if not exposures_raw:
        logger.warning("No sanctions exposure data for route: {}", route_id)
        return {
            "route_id": route_id,
            "risk_score": 0.0,
            "risk_level": "LOW",
            "primary_exposures": [],
            "compliance_cost_usd": 500,
            "due_diligence": _DUE_DILIGENCE["LOW"],
            "recommendation": "Standard due diligence. No known sanctions exposure.",
        }

    # Build per-exposure detail with matched SanctionsRegime data
    primary_exposures = []
    weighted_score_total = 0.0
    total_weight_sum = 0.0
    total_compliance_cost = 0

    for (country, impact, weight) in exposures_raw:
        impact_multiplier = _IMPACT_WEIGHTS.get(impact, 0.1)
        contribution = impact_multiplier * weight
        weighted_score_total += contribution
        total_weight_sum += weight

        # Find matching regime(s) for this country
        regimes = SANCTIONS_BY_COUNTRY.get(country, [])
        penalty = max((r.penalty_per_violation_usd for r in regimes), default=0)
        cost = max((r.compliance_cost_per_voyage_usd for r in regimes), default=500)
        total_compliance_cost += int(cost * weight)

        primary_exposures.append({
            "country": country,
            "shipping_impact": impact,
            "exposure_weight": weight,
            "risk_contribution": round(contribution, 3),
            "regimes": [r.regime_name for r in regimes],
            "max_penalty_usd": penalty,
        })

    # Normalise — cap at 1.0
    risk_score = min(1.0, weighted_score_total)
    risk_score = round(risk_score, 3)

    # Determine risk level
    risk_level = "LOW"
    for threshold, label in _SCORE_TO_RISK:
        if risk_score >= threshold:
            risk_level = label
            break

    # Sort exposures by contribution descending
    primary_exposures.sort(key=lambda x: x["risk_contribution"], reverse=True)

    # Cap compliance cost reasonably
    compliance_cost = min(15_000, max(500, total_compliance_cost))

    # Recommendation text
    if risk_level == "CRITICAL":
        recommendation = (
            "DO NOT fixture without legal clearance. OFAC/EU/UN penalties apply. "
            "Full enhanced due diligence and compliance officer sign-off mandatory."
        )
    elif risk_level == "HIGH":
        recommendation = (
            "Legal review strongly recommended before fixture. "
            "Enhanced screening of all counterparties, cargo, and vessel history required."
        )
    elif risk_level == "MODERATE":
        recommendation = (
            "Standard enhanced diligence sufficient. "
            "Screen cargo origin and counterparties against current sanctions lists."
        )
    else:
        recommendation = (
            "Standard due diligence. Low sanctions exposure on this route. "
            "Routine SDN/EU list screening sufficient."
        )

    result = {
        "route_id": route_id,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "primary_exposures": primary_exposures,
        "compliance_cost_usd": compliance_cost,
        "due_diligence": _DUE_DILIGENCE[risk_level],
        "recommendation": recommendation,
    }

    logger.info(
        "Route {} compliance risk: {} (score={:.3f}, cost=${})",
        route_id, risk_level, risk_score, compliance_cost,
    )
    return result


def get_all_route_compliance_scores() -> List[tuple]:
    """Return list of (route_id, risk_score, risk_level) for all 17 routes, sorted by score desc."""
    all_ids = list(_ROUTE_SANCTIONS_EXPOSURE.keys())
    results = []
    for rid in all_ids:
        r = compute_route_compliance_risk(rid)
        results.append((rid, r["risk_score"], r["risk_level"]))
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def get_high_risk_sanctions_alerts() -> List[SanctionsRegime]:
    """Return all CRITICAL and HIGH risk sanctions regimes."""
    return [s for s in ACTIVE_SANCTIONS if s.risk_level in ("CRITICAL", "HIGH")]
