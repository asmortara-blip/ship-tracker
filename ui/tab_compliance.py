"""tab_compliance.py — Maritime Compliance Command Center.

Ten sections:
  1. Compliance Status Hero Dashboard  — Overall score gauge, KPI tiles, violation counts, deadlines
  2. IMO Regulation Tracker            — IMO 2020/2023/2025 progress bars, status badges per regulation
  3. CII Rating Cards                  — Carbon Intensity Indicator per vessel type with trend sparklines
  4. EU ETS Dashboard                  — Carbon allowance cost tracker, exposure cards, compliance position
  5. Sanctions Screening Monitor       — Jurisdiction coverage, flagged entities, risk heatmap
  6. Port State Control Risk Cards     — Detention risk per port, historical detention rates
  7. Compliance Deadline Calendar      — Visual calendar of upcoming regulatory deadlines
  8. Flag State Compliance Ranking     — Compliance scores by flag state registry
  9. Documentation Status Tracker      — Certificate expiry alerts, renewal countdown
 10. Regulatory Change Feed            — Upcoming regulations with impact assessment

Data currency note:
  IMO Carbon Intensity Indicator (CII) reduction factors and rating thresholds are reviewed
  annually at MEPC sessions. EU ETS shipping scope and EUA prices change quarterly.
  OFAC/EU sanctions designations are updated continuously. Always verify against the
  latest MEPC circulars, EUR-Lex OJ publications, and OFAC SDN list before operational use.
"""
from __future__ import annotations

import datetime
import math

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from processing.sanctions_tracker import (
    ACTIVE_SANCTIONS,
    DARK_FLEET_2025,
    SanctionsRegime,
    compute_route_compliance_risk,
    get_all_route_compliance_scores,
)

# ---------------------------------------------------------------------------
# Colour palette — matches app-wide dark theme
# ---------------------------------------------------------------------------

C_BG      = "#0a0f1a"
C_CARD    = "#1a2235"
C_CARD2   = "#141d30"
C_BORDER  = "rgba(255,255,255,0.08)"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"
C_HIGH    = "#10b981"   # green  — safe / compliant
C_ACCENT  = "#3b82f6"   # blue   — accent / info
C_WARN    = "#f59e0b"   # amber  — moderate / restricted
C_DANGER  = "#ef4444"   # red    — critical / prohibited
C_ORANGE  = "#f97316"   # orange — high / restricted
C_PURPLE  = "#7c3aed"   # purple — OFAC / authority
C_TEAL    = "#0d9488"   # teal   — EU ETS

# IMO CII rating colours
_CII_COLORS: dict[str, str] = {
    "A": "#10b981",
    "B": "#22d3ee",
    "C": "#f59e0b",
    "D": "#f97316",
    "E": "#ef4444",
}

# ---------------------------------------------------------------------------
# Static compliance data — IMO regulations
# ---------------------------------------------------------------------------

_IMO_REGULATIONS = [
    {
        "id": "imo_2020_sulphur",
        "name": "IMO 2020 — Global Sulphur Cap",
        "authority": "IMO / MARPOL Annex VI",
        "effective": datetime.date(2020, 1, 1),
        "requirement": "≤0.50% sulphur in fuel oil (ECA: ≤0.10%)",
        "status": "ACTIVE",
        "fleet_compliance_pct": 97.4,
        "description": "Global cap on sulphur content of fuel oil used by ships. Scrubbers (EGCS) provide an equivalent compliance pathway.",
        "penalty_usd": 250_000,
        "category": "Emissions",
        "badge_color": C_HIGH,
    },
    {
        "id": "imo_2023_cii",
        "name": "IMO 2023 — CII & EEXI Mandatory",
        "authority": "IMO / MARPOL Annex VI",
        "effective": datetime.date(2023, 1, 1),
        "requirement": "Annual CII rating (A–E) + EEXI attainment certificate",
        "status": "ACTIVE",
        "fleet_compliance_pct": 84.1,
        "description": "Carbon Intensity Indicator (CII) mandatory rating from 2023. Ships rated D/E for 3 consecutive years must submit a SEEMP corrective plan.",
        "penalty_usd": 0,
        "category": "Carbon",
        "badge_color": C_WARN,
    },
    {
        "id": "imo_2023_mepc80",
        "name": "MEPC 80 — GHG Strategy Revision",
        "authority": "IMO MEPC 80",
        "effective": datetime.date(2023, 7, 7),
        "requirement": "Net-zero GHG by 2050; 20% reduction by 2030 vs 2008",
        "status": "ACTIVE",
        "fleet_compliance_pct": 61.3,
        "description": "Revised IMO GHG Strategy adopted at MEPC 80. Binding milestone targets replacing the initial 2018 strategy. Mid-term measures under development.",
        "penalty_usd": 0,
        "category": "GHG Strategy",
        "badge_color": C_WARN,
    },
    {
        "id": "imo_2024_biofouling",
        "name": "IMO 2024 — Biofouling Guidelines",
        "authority": "IMO MEPC 81",
        "effective": datetime.date(2024, 6, 1),
        "requirement": "Biofouling management plans mandatory for MARPOL ships",
        "status": "ACTIVE",
        "fleet_compliance_pct": 71.8,
        "description": "Revised biofouling management guidelines adopted at MEPC 81. Mandatory for ships >400 GT trading internationally.",
        "penalty_usd": 100_000,
        "category": "Environmental",
        "badge_color": C_WARN,
    },
    {
        "id": "imo_2025_fuel_eu",
        "name": "FuelEU Maritime — Jan 2025",
        "authority": "EU Regulation 2023/1805",
        "effective": datetime.date(2025, 1, 1),
        "requirement": "GHG intensity of energy used ≤2% reduction vs 2020 baseline",
        "status": "ACTIVE",
        "fleet_compliance_pct": 78.5,
        "description": "FuelEU Maritime Regulation applies to ships ≥5,000 GT calling EU/EEA ports. GHG intensity target tightens progressively through 2050.",
        "penalty_usd": 2_400_000,
        "category": "Fuel",
        "badge_color": C_ACCENT,
    },
    {
        "id": "imo_2026_ballast",
        "name": "BWM Convention — D-2 Standard",
        "authority": "IMO BWM Convention",
        "effective": datetime.date(2024, 9, 8),
        "requirement": "All vessels must meet D-2 biological treatment standard",
        "status": "TRANSITION",
        "fleet_compliance_pct": 88.2,
        "description": "Ballast Water Management Convention D-2 standard now mandatory for all ships. D-1 (exchange) no longer accepted by most PSC authorities.",
        "penalty_usd": 150_000,
        "category": "Environmental",
        "badge_color": C_HIGH,
    },
    {
        "id": "imo_2027_cii_tighten",
        "name": "CII 2027 — Tightened Reduction Factor",
        "authority": "IMO / MARPOL Annex VI",
        "effective": datetime.date(2027, 1, 1),
        "requirement": "CII reduction factor increases — estimated +5% stringency",
        "status": "UPCOMING",
        "fleet_compliance_pct": 0.0,
        "description": "Annual CII reduction factors are reviewed at MEPC. 2027 factors expected to increase required efficiency improvements, pushing more vessels into D/E rating.",
        "penalty_usd": 0,
        "category": "Carbon",
        "badge_color": C_ORANGE,
    },
    {
        "id": "imo_2030_ghg",
        "name": "IMO 2030 — GHG Milestone",
        "authority": "IMO GHG Strategy",
        "effective": datetime.date(2030, 1, 1),
        "requirement": "≥20% GHG reduction vs 2008 baseline (striving for 30%)",
        "status": "UPCOMING",
        "fleet_compliance_pct": 0.0,
        "description": "First binding IMO GHG milestone under revised 2023 strategy. Alternative fuels adoption (ammonia, methanol, LNG) critical for compliance pathway.",
        "penalty_usd": 0,
        "category": "GHG Strategy",
        "badge_color": C_DANGER,
    },
]

# ---------------------------------------------------------------------------
# CII vessel type reference data
# ---------------------------------------------------------------------------

_CII_VESSEL_TYPES = [
    {
        "type": "Containership",
        "dwt_range": "8,000–240,000 DWT",
        "current_rating": "B",
        "trend": [72, 69, 65, 61, 58, 54],
        "attained_cii": 8.2,
        "required_cii": 9.1,
        "ref_year": 2023,
        "note": "Slow steaming + fuel optimisation driving improvement",
        "yoy_change": -4.8,
        "icon": "🚢",
    },
    {
        "type": "Bulk Carrier",
        "dwt_range": "10,000–400,000 DWT",
        "current_rating": "C",
        "trend": [81, 79, 76, 74, 73, 71],
        "attained_cii": 6.4,
        "required_cii": 5.9,
        "ref_year": 2023,
        "note": "Age profile elevating emissions; newbuild programme needed",
        "yoy_change": -2.1,
        "icon": "⚓",
    },
    {
        "type": "Tanker (VLCC)",
        "dwt_range": "200,000–320,000 DWT",
        "current_rating": "A",
        "trend": [55, 52, 49, 46, 43, 41],
        "attained_cii": 4.1,
        "required_cii": 5.2,
        "ref_year": 2023,
        "note": "Eco-efficient fleet renewal driving strong performance",
        "yoy_change": -6.2,
        "icon": "🛢",
    },
    {
        "type": "LNG Carrier",
        "dwt_range": "70,000–267,000 m³",
        "current_rating": "D",
        "trend": [62, 65, 68, 71, 69, 74],
        "attained_cii": 12.3,
        "required_cii": 9.8,
        "ref_year": 2023,
        "note": "Methane slip inflating carbon intensity scores",
        "yoy_change": 3.1,
        "icon": "🔵",
    },
    {
        "type": "General Cargo",
        "dwt_range": "3,000–40,000 DWT",
        "current_rating": "E",
        "trend": [95, 93, 91, 90, 88, 87],
        "attained_cii": 22.7,
        "required_cii": 15.1,
        "ref_year": 2023,
        "note": "Older fleet — SEEMP corrective action plan mandatory",
        "yoy_change": -1.4,
        "icon": "📦",
    },
    {
        "type": "Ro-Ro / Car Carrier",
        "dwt_range": "5,000–80,000 DWT",
        "current_rating": "C",
        "trend": [68, 66, 65, 64, 63, 62],
        "attained_cii": 11.4,
        "required_cii": 10.8,
        "ref_year": 2023,
        "note": "Wind-assisted propulsion retrofits showing moderate gains",
        "yoy_change": -1.8,
        "icon": "🚗",
    },
]

# ---------------------------------------------------------------------------
# EU ETS data
# ---------------------------------------------------------------------------

_EU_ETS = {
    "eua_price_eur": 68.40,
    "eua_price_change_pct": -3.2,
    "phase_in_2024_pct": 40,
    "phase_in_2025_pct": 70,
    "phase_in_2026_pct": 100,
    "current_phase_in_pct": 70,
    "fleet_allowances_allocated": 0,
    "estimated_annual_cost_usd_m": 14.7,
    "verified_emissions_mt": 842_000,
    "allowances_purchased": 589_400,
    "shortfall_allowances": 0,
    "routes_in_scope": 11,
    "routes_total": 17,
}

_EU_ETS_ROUTES = [
    {"route": "Asia-Europe", "scope": "50% voyages EU", "annual_mt_co2": 182_000, "cost_usd_m": 3.2, "status": "IN_SCOPE"},
    {"route": "Transatlantic", "scope": "100% voyages EU-EU", "annual_mt_co2": 98_000, "cost_usd_m": 1.7, "status": "IN_SCOPE"},
    {"route": "Mediterranean Hub to Asia", "scope": "50% voyages EU", "annual_mt_co2": 74_000, "cost_usd_m": 1.3, "status": "IN_SCOPE"},
    {"route": "North Africa/Med to Europe", "scope": "50% voyages EU", "annual_mt_co2": 61_000, "cost_usd_m": 1.1, "status": "IN_SCOPE"},
    {"route": "Europe to South America", "scope": "50% voyages EU", "annual_mt_co2": 55_000, "cost_usd_m": 0.96, "status": "IN_SCOPE"},
    {"route": "Trans-Pacific Eastbound", "scope": "Out of scope", "annual_mt_co2": 0, "cost_usd_m": 0.0, "status": "OUT_SCOPE"},
    {"route": "Middle East Hub to Asia", "scope": "Out of scope", "annual_mt_co2": 0, "cost_usd_m": 0.0, "status": "OUT_SCOPE"},
]

# ---------------------------------------------------------------------------
# Port State Control detention risk data
# ---------------------------------------------------------------------------

_PSC_PORTS = [
    {
        "port": "Shanghai (CNSHA)",
        "country": "China",
        "psc_regime": "Tokyo MOU",
        "detention_rate_pct": 2.1,
        "inspections_2024": 4_820,
        "deficiencies_avg": 1.8,
        "risk_level": "LOW",
        "top_deficiency": "Fire-fighting equipment",
        "flag_risk_factor": 1.0,
        "note": "Stringent but well-documented inspection criteria",
    },
    {
        "port": "Rotterdam (NLRTM)",
        "country": "Netherlands",
        "psc_regime": "Paris MOU",
        "detention_rate_pct": 3.7,
        "inspections_2024": 2_941,
        "deficiencies_avg": 2.3,
        "risk_level": "MODERATE",
        "top_deficiency": "ISM / SMS documentation",
        "flag_risk_factor": 1.1,
        "note": "EU port — increased environmental scrutiny post-2025",
    },
    {
        "port": "Singapore (SGSIN)",
        "country": "Singapore",
        "psc_regime": "Tokyo MOU",
        "detention_rate_pct": 1.4,
        "inspections_2024": 3_680,
        "deficiencies_avg": 1.2,
        "risk_level": "LOW",
        "top_deficiency": "Life-saving appliances",
        "flag_risk_factor": 1.0,
        "note": "World's most efficient PSC regime. Low detention, high throughput",
    },
    {
        "port": "Houston (USHOU)",
        "country": "United States",
        "psc_regime": "US Coast Guard",
        "detention_rate_pct": 5.9,
        "inspections_2024": 1_870,
        "deficiencies_avg": 3.1,
        "risk_level": "HIGH",
        "top_deficiency": "MARPOL / pollution prevention",
        "flag_risk_factor": 1.4,
        "note": "USCG maintains zero-tolerance on MARPOL violations. Criminal prosecution risk",
    },
    {
        "port": "Piraeus (GRPIR)",
        "country": "Greece",
        "psc_regime": "Paris MOU",
        "detention_rate_pct": 4.2,
        "inspections_2024": 1_340,
        "deficiencies_avg": 2.7,
        "risk_level": "MODERATE",
        "top_deficiency": "Manning / crew certification",
        "flag_risk_factor": 1.1,
        "note": "Paris MOU concentrated inspection campaigns targeting bulk carriers",
    },
    {
        "port": "Santos (BRSSZ)",
        "country": "Brazil",
        "psc_regime": "Viña del Mar MOU",
        "detention_rate_pct": 7.3,
        "inspections_2024": 892,
        "deficiencies_avg": 3.8,
        "risk_level": "HIGH",
        "top_deficiency": "Structural / watertight integrity",
        "flag_risk_factor": 1.3,
        "note": "Elevated detention rate — pre-call documentation review strongly advised",
    },
    {
        "port": "Durban (ZADUR)",
        "country": "South Africa",
        "psc_regime": "Indian Ocean MOU",
        "detention_rate_pct": 9.1,
        "inspections_2024": 614,
        "deficiencies_avg": 4.6,
        "risk_level": "CRITICAL",
        "top_deficiency": "Fire detection / suppression systems",
        "flag_risk_factor": 1.6,
        "note": "Highest regional detention rate. Allow extra port time for older vessels",
    },
    {
        "port": "Dubai (AEDXB)",
        "country": "UAE",
        "psc_regime": "Riyadh MOU",
        "detention_rate_pct": 3.1,
        "inspections_2024": 1_280,
        "deficiencies_avg": 2.0,
        "risk_level": "MODERATE",
        "top_deficiency": "Navigation / bridge equipment",
        "flag_risk_factor": 1.0,
        "note": "Busy hub — inspection queue management important",
    },
]

# ---------------------------------------------------------------------------
# Flag state compliance rankings
# ---------------------------------------------------------------------------

_FLAG_STATES = [
    {"flag": "Marshall Islands", "iso": "MHL", "registry_type": "Open",   "fleet_gt": 240_000_000, "psc_detention_rate": 1.2, "white_list": True,  "imo_audit": "Passed 2023", "compliance_score": 96},
    {"flag": "Liberia",          "iso": "LBR", "registry_type": "Open",   "fleet_gt": 230_000_000, "psc_detention_rate": 1.5, "white_list": True,  "imo_audit": "Passed 2022", "compliance_score": 94},
    {"flag": "Panama",           "iso": "PAN", "registry_type": "Open",   "fleet_gt": 380_000_000, "psc_detention_rate": 2.1, "white_list": True,  "imo_audit": "Passed 2023", "compliance_score": 91},
    {"flag": "Bahamas",          "iso": "BHS", "registry_type": "Open",   "fleet_gt": 80_000_000,  "psc_detention_rate": 1.8, "white_list": True,  "imo_audit": "Passed 2022", "compliance_score": 92},
    {"flag": "Singapore",        "iso": "SGP", "registry_type": "National","fleet_gt": 95_000_000, "psc_detention_rate": 0.8, "white_list": True,  "imo_audit": "Passed 2024", "compliance_score": 98},
    {"flag": "Norway",           "iso": "NOR", "registry_type": "NIS",    "fleet_gt": 40_000_000,  "psc_detention_rate": 0.6, "white_list": True,  "imo_audit": "Passed 2023", "compliance_score": 99},
    {"flag": "Greece",           "iso": "GRC", "registry_type": "National","fleet_gt": 52_000_000, "psc_detention_rate": 1.1, "white_list": True,  "imo_audit": "Passed 2022", "compliance_score": 95},
    {"flag": "Malta",            "iso": "MLT", "registry_type": "Open",   "fleet_gt": 90_000_000,  "psc_detention_rate": 2.4, "white_list": True,  "imo_audit": "Passed 2023", "compliance_score": 89},
    {"flag": "Cyprus",           "iso": "CYP", "registry_type": "Open",   "fleet_gt": 22_000_000,  "psc_detention_rate": 3.1, "white_list": False, "imo_audit": "Due 2025",    "compliance_score": 82},
    {"flag": "China",            "iso": "CHN", "registry_type": "National","fleet_gt": 70_000_000, "psc_detention_rate": 2.8, "white_list": True,  "imo_audit": "Passed 2022", "compliance_score": 86},
    {"flag": "Palau",            "iso": "PLW", "registry_type": "Open",   "fleet_gt": 3_200_000,   "psc_detention_rate": 8.4, "white_list": False, "imo_audit": "Overdue",     "compliance_score": 58},
    {"flag": "Togo",             "iso": "TGO", "registry_type": "Open",   "fleet_gt": 2_800_000,   "psc_detention_rate": 9.2, "white_list": False, "imo_audit": "Overdue",     "compliance_score": 52},
    {"flag": "Cameroon",         "iso": "CMR", "registry_type": "Open",   "fleet_gt": 1_400_000,   "psc_detention_rate": 11.8,"white_list": False, "imo_audit": "Overdue",     "compliance_score": 41},
]

# ---------------------------------------------------------------------------
# Documentation tracker
# ---------------------------------------------------------------------------

_TODAY = datetime.date(2026, 3, 20)

_DOCUMENTS = [
    {"doc": "Safety Management Certificate (SMC)",    "vessel": "MV Pacific Star",   "issued": datetime.date(2021, 8, 15), "expires": datetime.date(2026, 8, 15),  "category": "Safety",        "authority": "Flag State",   "renewal_lead_days": 90},
    {"doc": "Document of Compliance (DOC)",           "vessel": "Fleet-wide",        "issued": datetime.date(2023, 11, 1), "expires": datetime.date(2026, 11, 1),  "category": "ISM",           "authority": "Class Society", "renewal_lead_days": 60},
    {"doc": "MARPOL Annex VI — IAPP Certificate",     "vessel": "MV Atlantic Rose",  "issued": datetime.date(2021, 3, 10), "expires": datetime.date(2026, 3, 10),  "category": "Emissions",     "authority": "Flag State",   "renewal_lead_days": 90},
    {"doc": "International Load Line Certificate",    "vessel": "MV Northern Dawn",  "issued": datetime.date(2024, 9, 22), "expires": datetime.date(2029, 9, 22),  "category": "Structural",    "authority": "Class Society", "renewal_lead_days": 60},
    {"doc": "Ballast Water Management Certificate",   "vessel": "MV Pacific Star",   "issued": datetime.date(2023, 6, 1),  "expires": datetime.date(2026, 5, 31),  "category": "Environmental", "authority": "Flag State",   "renewal_lead_days": 120},
    {"doc": "ISPS Ship Security Certificate (ISSC)",  "vessel": "MV Atlantic Rose",  "issued": datetime.date(2021, 7, 4),  "expires": datetime.date(2026, 7, 4),   "category": "Security",      "authority": "Flag State",   "renewal_lead_days": 90},
    {"doc": "Oil Pollution Prevention Certificate",   "vessel": "MV Southern Cross", "issued": datetime.date(2020, 12, 1), "expires": datetime.date(2026, 4, 15),  "category": "Environmental", "authority": "Flag State",   "renewal_lead_days": 90},
    {"doc": "Seafarers' Employment Agreement (SEA)",  "vessel": "Fleet-wide",        "issued": datetime.date(2025, 6, 1),  "expires": datetime.date(2026, 6, 1),   "category": "Manning",       "authority": "Flag State",   "renewal_lead_days": 60},
    {"doc": "CII Annual Rating Declaration",          "vessel": "Fleet-wide",        "issued": datetime.date(2025, 4, 30), "expires": datetime.date(2026, 4, 30),  "category": "Carbon",        "authority": "IMO / Flag",   "renewal_lead_days": 30},
    {"doc": "EU ETS Monitoring Plan Approval",        "vessel": "Fleet-wide",        "issued": datetime.date(2023, 12, 15),"expires": datetime.date(2026, 12, 15), "category": "Carbon",        "authority": "MRV Verifier", "renewal_lead_days": 60},
    {"doc": "P&I Club Insurance Certificate",         "vessel": "Fleet-wide",        "issued": datetime.date(2025, 2, 20), "expires": datetime.date(2026, 2, 20),  "category": "Insurance",     "authority": "P&I Club",     "renewal_lead_days": 90},
    {"doc": "Class Renewal Survey",                   "vessel": "MV Northern Dawn",  "issued": datetime.date(2021, 5, 5),  "expires": datetime.date(2026, 5, 5),   "category": "Structural",    "authority": "Class Society", "renewal_lead_days": 180},
]

# ---------------------------------------------------------------------------
# Regulatory change feed
# ---------------------------------------------------------------------------

_REG_CHANGES = [
    {
        "regulation": "FuelEU Maritime — 2025 Phase In",
        "authority": "European Commission",
        "effective": datetime.date(2025, 1, 1),
        "status": "IN_FORCE",
        "impact": "HIGH",
        "vessels_affected": "All >5,000 GT calling EU ports",
        "description": "70% phase-in of EU ETS obligations for shipping. Verified emissions reporting mandatory. Non-compliance penalty: €100/tonne excess CO₂.",
        "action_required": "Submit verified MRV report; purchase EUAs for covered emissions",
        "cost_impact_usd_m": 14.7,
    },
    {
        "regulation": "IMO Biofouling Management — Mandatory",
        "authority": "IMO MEPC 81",
        "effective": datetime.date(2024, 6, 1),
        "status": "IN_FORCE",
        "impact": "MODERATE",
        "vessels_affected": "All MARPOL ships >400 GT",
        "description": "Revised biofouling management guidelines adopted. Anti-fouling system performance reporting now mandatory under Paris/Tokyo MOU inspections.",
        "action_required": "Update biofouling management plans; log hull cleaning dates",
        "cost_impact_usd_m": 0.8,
    },
    {
        "regulation": "CII Annual Reduction Factor 2026",
        "authority": "IMO / MARPOL Annex VI",
        "effective": datetime.date(2026, 1, 1),
        "status": "IN_FORCE",
        "impact": "HIGH",
        "vessels_affected": "All vessels >5,000 GT",
        "description": "2026 CII reduction factors applied. Required CII values tighten by ~2% versus 2025 baseline. Vessels currently rated C face increased D/E risk.",
        "action_required": "Review vessel SEEMP; consider speed/trim optimisation or fuel switching",
        "cost_impact_usd_m": 3.2,
    },
    {
        "regulation": "EU ETS — 100% Phase In",
        "authority": "European Commission",
        "effective": datetime.date(2026, 1, 1),
        "status": "IN_FORCE",
        "impact": "CRITICAL",
        "vessels_affected": "All >5,000 GT with EU port calls",
        "description": "Full 100% phase-in of EU ETS for shipping from 2026. Both intra-EU voyages (100%) and extra-EU voyages (50%) fully covered. EUA costs will double vs 2024.",
        "action_required": "Secure EUA forward purchasing strategy; update voyage pricing to include ETS cost",
        "cost_impact_usd_m": 21.0,
    },
    {
        "regulation": "BWTS D-2 — All Ships Deadline",
        "authority": "IMO BWM Convention",
        "effective": datetime.date(2024, 9, 8),
        "status": "IN_FORCE",
        "impact": "MODERATE",
        "vessels_affected": "All vessels with ballast tanks",
        "description": "Final deadline for D-2 compliance. D-1 (exchange) no longer accepted. BWTS installation required or vessel barred from international trade.",
        "action_required": "Confirm BWTS type-approval certificate onboard; update BWM plan",
        "cost_impact_usd_m": 1.2,
    },
    {
        "regulation": "IMO Mid-Term GHG Measures",
        "authority": "IMO MEPC 83",
        "effective": datetime.date(2027, 1, 1),
        "status": "UPCOMING",
        "impact": "CRITICAL",
        "vessels_affected": "All international shipping",
        "description": "IMO mid-term GHG measures under final development at MEPC 83/84. Expected to include a global carbon levy (~$18-150/tonne CO₂) and fuel standard.",
        "action_required": "Monitor MEPC 83 outcome (Oct 2025); scenario-plan for carbon levy $18-150/t",
        "cost_impact_usd_m": 45.0,
    },
    {
        "regulation": "CII 2027 Tightened Factor",
        "authority": "IMO / MARPOL Annex VI",
        "effective": datetime.date(2027, 1, 1),
        "status": "UPCOMING",
        "impact": "HIGH",
        "vessels_affected": "All vessels >5,000 GT",
        "description": "CII reduction factors for 2027 expected to increase stringency by ~5%. Vessels currently borderline C/D will likely slip to D or E.",
        "action_required": "Initiate fleet efficiency investment planning; evaluate alternative fuel options",
        "cost_impact_usd_m": 6.8,
    },
    {
        "regulation": "Poseidon Principles — Alignment Review",
        "authority": "Poseidon Principles (24 banks)",
        "effective": datetime.date(2026, 6, 1),
        "status": "UPCOMING",
        "impact": "HIGH",
        "vessels_affected": "All financed vessels",
        "description": "Annual Poseidon Principles portfolio alignment disclosure. Vessels with poor CII alignment risk finance cost premium or covenant breach.",
        "action_required": "Submit verified CII data to lenders; ensure IMO DCS data is complete",
        "cost_impact_usd_m": 2.1,
    },
]

# ---------------------------------------------------------------------------
# Compliance deadline calendar events
# ---------------------------------------------------------------------------

_DEADLINE_EVENTS = [
    {"date": datetime.date(2026, 3, 31), "title": "Q1 EU ETS Monitoring Report Due", "category": "EU ETS",    "urgency": "CRITICAL", "description": "Q1 2026 verified emissions report submission to EU administering authority"},
    {"date": datetime.date(2026, 4, 15), "title": "Oil Pollution Cert Renewal",       "category": "Document", "urgency": "CRITICAL", "description": "MV Southern Cross MARPOL oil pollution certificate expires — renewal required"},
    {"date": datetime.date(2026, 4, 30), "title": "CII Annual Declaration",           "category": "CII",      "urgency": "HIGH",     "description": "Fleet-wide CII annual rating declaration due to flag state"},
    {"date": datetime.date(2026, 5, 5),  "title": "Class Renewal Survey — N. Dawn",  "category": "Class",    "urgency": "HIGH",     "description": "MV Northern Dawn 5-year class renewal survey window opens"},
    {"date": datetime.date(2026, 5, 31), "title": "Ballast Water Cert Expiry",        "category": "Document", "urgency": "HIGH",     "description": "MV Pacific Star ballast water management certificate expires"},
    {"date": datetime.date(2026, 6, 1),  "title": "SEA Fleet Renewal",               "category": "Manning",  "urgency": "MODERATE", "description": "Seafarers Employment Agreements fleet-wide renewal due"},
    {"date": datetime.date(2026, 6, 30), "title": "EU ETS Annual Reconciliation",     "category": "EU ETS",   "urgency": "CRITICAL", "description": "Annual surrender of EUAs equal to verified 2025 emissions — hard deadline"},
    {"date": datetime.date(2026, 7, 4),  "title": "ISSC Renewal — Atlantic Rose",    "category": "Document", "urgency": "HIGH",     "description": "MV Atlantic Rose ISPS Ship Security Certificate expires"},
    {"date": datetime.date(2026, 8, 15), "title": "SMC Renewal — Pacific Star",      "category": "Document", "urgency": "HIGH",     "description": "MV Pacific Star Safety Management Certificate expires"},
    {"date": datetime.date(2026, 9, 30), "title": "Q2/Q3 ETS Interim Review",        "category": "EU ETS",   "urgency": "MODERATE", "description": "Internal review of ETS position ahead of Q4 reconciliation"},
    {"date": datetime.date(2026, 11, 1), "title": "DOC Fleet Renewal",               "category": "ISM",      "urgency": "HIGH",     "description": "Document of Compliance fleet-wide renewal due to class society"},
    {"date": datetime.date(2026, 12, 15),"title": "EU ETS Monitoring Plan Review",   "category": "EU ETS",   "urgency": "MODERATE", "description": "Fleet EU ETS monitoring plan annual review and reapproval"},
]

# ---------------------------------------------------------------------------
# Helper renderers
# ---------------------------------------------------------------------------

def _section_title(text: str, subtitle: str = "") -> None:
    try:
        st.markdown(
            f"<div style='margin:28px 0 6px'>"
            f"<span style='font-size:1.1rem; font-weight:700; color:{C_TEXT}; letter-spacing:0.02em'>{text}</span>"
            + (
                f"<div style='font-size:0.78rem; color:{C_TEXT3}; margin-top:4px; line-height:1.5'>{subtitle}</div>"
                if subtitle
                else ""
            )
            + "</div>",
            unsafe_allow_html=True,
        )
    except Exception:
        st.subheader(text)


def _kpi_card(label: str, value: str, delta: str = "", color: str = C_TEXT, border_color: str = C_BORDER, icon: str = "") -> None:
    try:
        delta_html = ""
        if delta:
            d_color = C_HIGH if delta.startswith("+") or delta.startswith("▲") else C_DANGER if delta.startswith("-") or delta.startswith("▼") else C_TEXT2
            delta_html = f"<div style='font-size:0.72rem; color:{d_color}; margin-top:4px'>{delta}</div>"
        st.markdown(
            f"<div style='background:{C_CARD}; border:1px solid {border_color}; border-radius:12px;"
            f" padding:16px 18px; text-align:center; height:100%'>"
            f"<div style='font-size:1.1rem; margin-bottom:4px'>{icon}</div>"
            f"<div style='font-size:1.65rem; font-weight:800; color:{color}; line-height:1.1'>{value}</div>"
            f"<div style='font-size:0.70rem; color:{C_TEXT2}; margin-top:5px; text-transform:uppercase; letter-spacing:0.05em'>{label}</div>"
            f"{delta_html}"
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception:
        st.metric(label=label, value=value)


def _badge(text: str, color: str = C_ACCENT, bg_alpha: str = "22") -> str:
    try:
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        bg = f"rgba({r},{g},{b},0.15)"
        return (
            f"<span style='background:{bg}; border:1px solid {color}; color:{color};"
            f" border-radius:4px; padding:2px 8px; font-size:0.68rem; font-weight:700;"
            f" letter-spacing:0.06em; white-space:nowrap'>{text}</span>"
        )
    except Exception:
        return f"[{text}]"


def _progress_bar(pct: float, color: str = C_HIGH, height: int = 8, label: str = "") -> str:
    try:
        pct = max(0.0, min(100.0, pct))
        bar = (
            f"<div style='background:rgba(255,255,255,0.08); border-radius:99px; height:{height}px; overflow:hidden; position:relative'>"
            f"<div style='width:{pct:.1f}%; background:{color}; height:100%; border-radius:99px; transition:width 0.4s'></div>"
            f"</div>"
        )
        if label:
            bar = f"<div style='display:flex; justify-content:space-between; margin-bottom:4px'><span style='font-size:0.72rem; color:{C_TEXT2}'>{label}</span><span style='font-size:0.72rem; color:{color}; font-weight:700'>{pct:.1f}%</span></div>" + bar
        return bar
    except Exception:
        return ""


def _days_until(d: datetime.date) -> int:
    return (d - _TODAY).days


def _urgency_color(days: int) -> str:
    if days < 0:
        return C_DANGER
    if days <= 30:
        return C_DANGER
    if days <= 90:
        return C_ORANGE
    if days <= 180:
        return C_WARN
    return C_HIGH


# ---------------------------------------------------------------------------
# Section 1 — Compliance Status Hero Dashboard
# ---------------------------------------------------------------------------

def _render_hero_dashboard() -> None:
    try:
        # --- compute overall compliance score ---
        regs_active      = [r for r in _IMO_REGULATIONS if r["status"] == "ACTIVE"]
        avg_fleet_pct    = sum(r["fleet_compliance_pct"] for r in regs_active) / max(len(regs_active), 1)
        docs_expiring_90 = sum(1 for d in _DOCUMENTS if 0 <= _days_until(d["expires"]) <= 90)
        docs_expired     = sum(1 for d in _DOCUMENTS if _days_until(d["expires"]) < 0)
        critical_regimes = sum(1 for s in ACTIVE_SANCTIONS if s.risk_level == "CRITICAL")
        violations_24    = sum(s.enforcement_cases_2024 for s in ACTIVE_SANCTIONS)
        deadlines_30d    = sum(1 for e in _DEADLINE_EVENTS if 0 <= _days_until(e["date"]) <= 30)

        # Composite score: weight fleet compliance 50%, penalise for expired docs & critical sanctions
        score = avg_fleet_pct * 0.50
        score -= docs_expired * 5
        score -= docs_expiring_90 * 1.5
        score -= critical_regimes * 2
        score = max(0.0, min(100.0, score))

        score_color = C_HIGH if score >= 85 else C_WARN if score >= 65 else C_DANGER
        score_label = "COMPLIANT" if score >= 85 else "NEEDS ATTENTION" if score >= 65 else "AT RISK"

        # --- gauge chart ---
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=score,
            number={"suffix": "%", "font": {"size": 36, "color": score_color, "family": "monospace"}},
            gauge={
                "axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": C_TEXT3,
                         "tickfont": {"color": C_TEXT3, "size": 11}},
                "bar": {"color": score_color, "thickness": 0.30},
                "bgcolor": C_CARD,
                "borderwidth": 0,
                "steps": [
                    {"range": [0,  50], "color": "rgba(239,68,68,0.15)"},
                    {"range": [50, 75], "color": "rgba(245,158,11,0.12)"},
                    {"range": [75,100], "color": "rgba(16,185,129,0.12)"},
                ],
                "threshold": {
                    "line": {"color": C_TEXT2, "width": 2},
                    "thickness": 0.75,
                    "value": 85,
                },
            },
            title={"text": f"<b>Overall Compliance Score</b><br><span style='font-size:14px;color:{score_color}'>{score_label}</span>",
                   "font": {"color": C_TEXT, "size": 14}},
        ))
        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            height=260,
            margin=dict(l=20, r=20, t=40, b=10),
        )

        col_gauge, col_kpis = st.columns([1, 2], gap="large")
        with col_gauge:
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

        with col_kpis:
            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
            k1, k2, k3 = st.columns(3)
            with k1:
                _kpi_card("Active Regulations", str(len(regs_active)), color=C_ACCENT, icon="📋",
                          border_color=f"rgba(59,130,246,0.3)")
            with k2:
                _kpi_card("Critical Sanctions", str(critical_regimes), color=C_DANGER, icon="🚨",
                          border_color=f"rgba(239,68,68,0.3)", delta="OFAC/EU/UN")
            with k3:
                _kpi_card("Enforcement Actions '24", f"{violations_24:,}", color=C_ORANGE, icon="⚖️",
                          border_color=f"rgba(249,115,22,0.3)")

            st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
            k4, k5, k6 = st.columns(3)
            with k4:
                _kpi_card("Docs Expiring ≤90d", str(docs_expiring_90),
                          color=C_WARN if docs_expiring_90 > 0 else C_HIGH, icon="📄",
                          border_color=f"rgba(245,158,11,0.3)" if docs_expiring_90 > 0 else C_BORDER)
            with k5:
                _kpi_card("Deadlines Next 30d", str(deadlines_30d),
                          color=C_DANGER if deadlines_30d > 0 else C_HIGH, icon="🗓",
                          border_color=f"rgba(239,68,68,0.3)" if deadlines_30d > 0 else C_BORDER)
            with k6:
                _kpi_card("Dark Fleet Vessels", f"~{DARK_FLEET_2025.estimated_vessels}", color=C_ORANGE, icon="🕶",
                          border_color=f"rgba(249,115,22,0.3)", delta="Non-compliant tonnage")

    except Exception as exc:
        logger.error(f"Hero dashboard error: {exc}")
        st.warning("Compliance hero dashboard temporarily unavailable.")


# ---------------------------------------------------------------------------
# Section 2 — IMO Regulation Tracker
# ---------------------------------------------------------------------------

def _render_imo_tracker() -> None:
    try:
        status_filter = st.radio(
            "Show regulations:",
            ["All", "Active", "Upcoming"],
            horizontal=True,
            key="imo_status_filter",
        )

        for reg in _IMO_REGULATIONS:
            if status_filter == "Active"   and reg["status"] not in ("ACTIVE", "TRANSITION"):
                continue
            if status_filter == "Upcoming" and reg["status"] != "UPCOMING":
                continue

            try:
                days_since = (_TODAY - reg["effective"]).days
                pct    = reg["fleet_compliance_pct"]
                s_col  = C_HIGH if reg["status"] == "ACTIVE" else C_ORANGE if reg["status"] == "TRANSITION" else C_ACCENT
                p_col  = C_HIGH if pct >= 90 else C_WARN if pct >= 70 else C_DANGER

                badge_html = _badge(reg["status"], s_col)
                cat_badge  = _badge(reg["category"], C_ACCENT)
                penalty_str = f"${reg['penalty_per_violation_usd']:,}" if reg["penalty_per_violation_usd"] else "Non-monetary"

                effective_str = reg["effective"].strftime("%d %b %Y")
                days_label = f"In force {days_since}d" if days_since >= 0 else f"In {abs(days_since)}d"

                progress_html = _progress_bar(pct, p_col, height=7,
                                              label=f"Fleet Compliance" if reg["status"] != "UPCOMING" else "Fleet Readiness") if reg["status"] != "UPCOMING" else ""

                st.markdown(
                    f"<div style='background:{C_CARD}; border:1px solid {C_BORDER}; border-left:3px solid {s_col};"
                    f" border-radius:10px; padding:16px 20px; margin-bottom:10px'>"
                    f"<div style='display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:8px'>"
                    f"<span style='font-size:0.95rem; font-weight:700; color:{C_TEXT}'>{reg['name']}</span>"
                    f"&nbsp;{badge_html}&nbsp;{cat_badge}"
                    f"</div>"
                    f"<div style='font-size:0.78rem; color:{C_TEXT2}; margin-bottom:10px'>{reg['description']}</div>"
                    f"<div style='display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; margin-bottom:10px'>"
                    f"<div><span style='font-size:0.68rem; color:{C_TEXT3}; text-transform:uppercase'>Authority</span><br>"
                    f"<span style='font-size:0.80rem; color:{C_TEXT}; font-weight:600'>{reg['authority']}</span></div>"
                    f"<div><span style='font-size:0.68rem; color:{C_TEXT3}; text-transform:uppercase'>Effective</span><br>"
                    f"<span style='font-size:0.80rem; color:{C_TEXT}'>{effective_str}</span> "
                    f"<span style='font-size:0.70rem; color:{C_TEXT3}'>({days_label})</span></div>"
                    f"<div><span style='font-size:0.68rem; color:{C_TEXT3}; text-transform:uppercase'>Max Penalty</span><br>"
                    f"<span style='font-size:0.80rem; color:{C_DANGER}; font-weight:600'>{penalty_str}</span></div>"
                    f"</div>"
                    f"<div style='font-size:0.78rem; color:{C_TEXT2}; background:rgba(255,255,255,0.03);"
                    f" border-radius:6px; padding:6px 10px; margin-bottom:10px'>"
                    f"<b style='color:{C_TEXT3}'>Requirement:</b> {reg['requirement']}</div>"
                    f"{progress_html}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            except Exception as inner_exc:
                logger.warning(f"IMO reg row error: {inner_exc}")
                continue

    except Exception as exc:
        logger.error(f"IMO tracker error: {exc}")
        st.warning("IMO regulation tracker temporarily unavailable.")


# ---------------------------------------------------------------------------
# Section 3 — CII Rating Cards
# ---------------------------------------------------------------------------

def _render_cii_cards() -> None:
    try:
        cols_per_row = 3
        vessel_chunks = [
            _CII_VESSEL_TYPES[i:i + cols_per_row]
            for i in range(0, len(_CII_VESSEL_TYPES), cols_per_row)
        ]

        for chunk in vessel_chunks:
            cols = st.columns(len(chunk), gap="medium")
            for col, vt in zip(cols, chunk):
                try:
                    rating      = vt["current_rating"]
                    r_color     = _CII_COLORS.get(rating, C_TEXT2)
                    trend       = vt["trend"]
                    yoy         = vt["yoy_change"]
                    trend_arrow = "▲" if yoy > 0 else "▼"
                    trend_color = C_DANGER if yoy > 0 else C_HIGH

                    # Sparkline
                    years = list(range(2018, 2018 + len(trend)))
                    fig_spark = go.Figure()
                    fig_spark.add_trace(go.Scatter(
                        x=years, y=trend,
                        mode="lines+markers",
                        line=dict(color=r_color, width=2),
                        marker=dict(size=5, color=r_color),
                        fill="tozeroy",
                        fillcolor=f"rgba({int(r_color[1:3],16)},{int(r_color[3:5],16)},{int(r_color[5:7],16)},0.12)",
                        hovertemplate="%{x}: %{y:.1f}g/DWT·nm<extra></extra>",
                    ))
                    fig_spark.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        height=90,
                        margin=dict(l=0, r=0, t=0, b=0),
                        xaxis=dict(visible=False),
                        yaxis=dict(visible=False),
                        showlegend=False,
                    )

                    with col:
                        st.markdown(
                            f"<div style='background:{C_CARD}; border:1px solid {C_BORDER}; border-top:3px solid {r_color};"
                            f" border-radius:12px; padding:16px 18px; margin-bottom:8px'>"
                            f"<div style='display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:4px'>"
                            f"<div>"
                            f"<div style='font-size:0.70rem; color:{C_TEXT3}; text-transform:uppercase; letter-spacing:0.06em'>{vt['icon']} {vt['type']}</div>"
                            f"<div style='font-size:0.72rem; color:{C_TEXT3}; margin-top:1px'>{vt['dwt_range']}</div>"
                            f"</div>"
                            f"<div style='background:{r_color}; color:#fff; border-radius:8px; width:42px; height:42px;"
                            f" display:flex; align-items:center; justify-content:center; font-size:1.4rem; font-weight:900'>{rating}</div>"
                            f"</div>"
                            f"<div style='font-size:0.78rem; color:{C_TEXT2}; margin-top:6px'>{vt['note']}</div>"
                            f"<div style='display:grid; grid-template-columns:1fr 1fr; gap:6px; margin-top:10px; margin-bottom:6px'>"
                            f"<div style='background:rgba(255,255,255,0.04); border-radius:6px; padding:6px 8px'>"
                            f"<div style='font-size:0.65rem; color:{C_TEXT3}'>Attained CII</div>"
                            f"<div style='font-size:0.88rem; font-weight:700; color:{r_color}'>{vt['attained_cii']} g/DWT·nm</div>"
                            f"</div>"
                            f"<div style='background:rgba(255,255,255,0.04); border-radius:6px; padding:6px 8px'>"
                            f"<div style='font-size:0.65rem; color:{C_TEXT3}'>Required CII</div>"
                            f"<div style='font-size:0.88rem; font-weight:700; color:{C_TEXT}'>{vt['required_cii']} g/DWT·nm</div>"
                            f"</div>"
                            f"</div>"
                            f"<div style='font-size:0.75rem; color:{trend_color}; font-weight:600; margin-top:2px'>"
                            f"{trend_arrow} {abs(yoy):.1f}% YoY</div>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                        st.plotly_chart(fig_spark, use_container_width=True, config={"displayModeBar": False},
                                        key=f"cii_spark_{vt['type'].replace(' ', '_')}")
                except Exception as inner_exc:
                    logger.warning(f"CII card error for {vt.get('type')}: {inner_exc}")

        # CII band reference
        st.markdown(
            "<div style='background:" + C_CARD + "; border:1px solid " + C_BORDER + "; border-radius:10px; padding:14px 18px; margin-top:12px'>"
            "<div style='font-size:0.80rem; font-weight:700; color:" + C_TEXT + "; margin-bottom:10px'>CII Rating Band Reference</div>"
            "<div style='display:flex; gap:8px; flex-wrap:wrap'>",
            unsafe_allow_html=True,
        )
        band_descriptions = [
            ("A", "Major superior — best 10th percentile", C_CII_A := "#10b981"),
            ("B", "Minor superior — better than average", "#22d3ee"),
            ("C", "Moderate — average CII performance",   "#f59e0b"),
            ("D", "Minor inferior — action plan if 3yr",  "#f97316"),
            ("E", "Inferior — SEEMP corrective plan req", "#ef4444"),
        ]
        bands_html = ""
        for rating_l, desc, bc in band_descriptions:
            bands_html += (
                f"<div style='background:rgba({int(bc[1:3],16)},{int(bc[3:5],16)},{int(bc[5:7],16)},0.12);"
                f" border:1px solid {bc}; border-radius:8px; padding:8px 12px; flex:1; min-width:120px'>"
                f"<div style='font-size:1.2rem; font-weight:900; color:{bc}; margin-bottom:4px'>{rating_l}</div>"
                f"<div style='font-size:0.70rem; color:{C_TEXT2}'>{desc}</div>"
                f"</div>"
            )
        st.markdown(bands_html + "</div></div>", unsafe_allow_html=True)

    except Exception as exc:
        logger.error(f"CII cards error: {exc}")
        st.warning("CII rating cards temporarily unavailable.")


# ---------------------------------------------------------------------------
# Section 4 — EU ETS Dashboard
# ---------------------------------------------------------------------------

def _render_eu_ets() -> None:
    try:
        ets = _EU_ETS
        eua_color = C_HIGH if ets["eua_price_change_pct"] >= 0 else C_DANGER

        # Top KPI row
        e1, e2, e3, e4 = st.columns(4)
        with e1:
            _kpi_card("EUA Price (€/t CO₂)",
                      f"€{ets['eua_price_eur']:.2f}",
                      delta=f"{'▲' if ets['eua_price_change_pct']>=0 else '▼'} {abs(ets['eua_price_change_pct']):.1f}% vs prev month",
                      color=C_TEAL, icon="💶", border_color="rgba(13,148,136,0.3)")
        with e2:
            _kpi_card("2025 Phase-In",
                      f"{ets['current_phase_in_pct']}%",
                      delta="→ 100% from Jan 2026",
                      color=C_WARN, icon="📈", border_color="rgba(245,158,11,0.3)")
        with e3:
            _kpi_card("Est. Annual Cost",
                      f"${ets['estimated_annual_cost_usd_m']:.1f}M",
                      delta="At current EUA pricing",
                      color=C_DANGER, icon="💸", border_color="rgba(239,68,68,0.3)")
        with e4:
            _kpi_card("Routes In Scope",
                      f"{ets['routes_in_scope']}/{ets['routes_total']}",
                      delta="EU port calls",
                      color=C_ACCENT, icon="🗺", border_color="rgba(59,130,246,0.3)")

        st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

        # Phase-in timeline bar
        st.markdown(
            f"<div style='background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:10px; padding:16px 20px; margin-bottom:14px'>"
            f"<div style='font-size:0.80rem; font-weight:700; color:{C_TEXT}; margin-bottom:12px'>EU ETS Phase-In Timeline</div>"
            f"<div style='display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px'>",
            unsafe_allow_html=True,
        )
        for year, pct, is_current in [
            ("2024", ets["phase_in_2024_pct"], False),
            ("2025", ets["phase_in_2025_pct"], True),
            ("2026", ets["phase_in_2026_pct"], False),
        ]:
            bar_color = C_TEAL if is_current else C_ACCENT if pct == 100 else C_TEXT3
            highlight = f"border:1px solid {C_TEAL};" if is_current else f"border:1px solid {C_BORDER};"
            curr_badge = f"&nbsp;{_badge('CURRENT', C_TEAL)}" if is_current else ""
            st.markdown(
                f"<div style='background:{C_CARD2}; {highlight} border-radius:8px; padding:12px'>"
                f"<div style='font-size:0.75rem; color:{C_TEXT2}; margin-bottom:6px'>{year}{curr_badge}</div>"
                f"{_progress_bar(pct, bar_color, height=10, label='ETS obligation')}"
                f"</div>",
                unsafe_allow_html=True,
            )
        st.markdown("</div></div>", unsafe_allow_html=True)

        # Route exposure table
        _section_title("Route ETS Exposure", "Emissions coverage and estimated annual ETS cost per route")
        for r in _EU_ETS_ROUTES:
            try:
                in_scope = r["status"] == "IN_SCOPE"
                row_color = C_BORDER if in_scope else "rgba(255,255,255,0.03)"
                status_badge = _badge("IN SCOPE", C_TEAL) if in_scope else _badge("OUT OF SCOPE", C_TEXT3)
                cost_str = f"${r['cost_usd_m']:.2f}M/yr" if in_scope else "—"
                co2_str  = f"{r['annual_mt_co2']:,} t CO₂" if in_scope else "—"
                st.markdown(
                    f"<div style='background:{C_CARD}; border:1px solid {row_color}; border-radius:8px;"
                    f" padding:10px 16px; margin-bottom:6px; display:flex; align-items:center; gap:12px; flex-wrap:wrap'>"
                    f"<span style='font-size:0.82rem; font-weight:600; color:{C_TEXT}; flex:2; min-width:160px'>{r['route']}</span>"
                    f"<span style='font-size:0.78rem; color:{C_TEXT2}; flex:1; min-width:120px'>{r['scope']}</span>"
                    f"<span style='font-size:0.80rem; color:{C_TEXT}; flex:1; min-width:110px'>{co2_str}</span>"
                    f"<span style='font-size:0.80rem; font-weight:700; color:{C_DANGER if in_scope else C_TEXT3}; flex:1; min-width:90px'>{cost_str}</span>"
                    f"{status_badge}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            except Exception:
                continue

    except Exception as exc:
        logger.error(f"EU ETS error: {exc}")
        st.warning("EU ETS dashboard temporarily unavailable.")


# ---------------------------------------------------------------------------
# Section 5 — Sanctions Screening Monitor
# ---------------------------------------------------------------------------

_SANCTION_COUNTRIES: list[dict] = [
    {"iso": "RUS", "name": "Russia",       "status": "PROHIBITED", "note": "Oil price cap + broad OFAC/EU measures. Dark fleet ~600 vessels."},
    {"iso": "IRN", "name": "Iran",         "status": "PROHIBITED", "note": "Near-total OFAC prohibition. Secondary sanctions on all dealings."},
    {"iso": "PRK", "name": "North Korea",  "status": "PROHIBITED", "note": "UN total embargo. STS transfers, AIS spoofing documented."},
    {"iso": "VEN", "name": "Venezuela",    "status": "RESTRICTED", "note": "OFAC sector sanctions on PdVSA oil. Some humanitarian exemptions."},
    {"iso": "CUB", "name": "Cuba",         "status": "RESTRICTED", "note": "US embargo. Non-US vessels barred from US ports 180 days post-Cuba call."},
    {"iso": "MMR", "name": "Myanmar",      "status": "RESTRICTED", "note": "Targeted military sanctions. Jade, timber, jet fuel prohibited."},
    {"iso": "SDN", "name": "Sudan",        "status": "MONITORED",  "note": "Active conflict. Darfur/Sudan OFAC sanctions. Humanitarian license required."},
    {"iso": "YEM", "name": "Yemen",        "status": "MONITORED",  "note": "Houthi SDGT designation. Red Sea attack zone. Operational + sanctions risk."},
    {"iso": "CHN", "name": "China",        "status": "MONITORED",  "note": "BIS Entity List / dual-use controls. Decoupling escalation risk."},
    {"iso": "BLR", "name": "Belarus",      "status": "MONITORED",  "note": "OFAC/EU sanctions on regime entities. Potash trade restricted."},
    {"iso": "SYR", "name": "Syria",        "status": "MONITORED",  "note": "OFAC Syria Sanctions Regulations. Broad trade restrictions in force."},
    {"iso": "LBY", "name": "Libya",        "status": "MONITORED",  "note": "UN arms embargo. Conflict-zone operational risk."},
    {"iso": "SOM", "name": "Somalia",      "status": "MONITORED",  "note": "Piracy risk zone. UN arms embargo on certain actors."},
]

_STATUS_COLOR_MAP: dict[str, str] = {
    "PROHIBITED": "#ef4444",
    "RESTRICTED": "#f97316",
    "MONITORED":  "#f59e0b",
    "CLEAR":      "#10b981",
}

_STATUS_SCORE_MAP: dict[str, float] = {
    "PROHIBITED": 1.0,
    "RESTRICTED": 0.6,
    "MONITORED":  0.3,
    "CLEAR":      0.0,
}


def _render_sanctions_monitor() -> None:
    try:
        # Jurisdiction summary KPIs
        prohibited_ct = sum(1 for c in _SANCTION_COUNTRIES if c["status"] == "PROHIBITED")
        restricted_ct = sum(1 for c in _SANCTION_COUNTRIES if c["status"] == "RESTRICTED")
        monitored_ct  = sum(1 for c in _SANCTION_COUNTRIES if c["status"] == "MONITORED")
        total_regimes = len(ACTIVE_SANCTIONS)

        s1, s2, s3, s4 = st.columns(4)
        with s1:
            _kpi_card("Prohibited Jurisdictions", str(prohibited_ct), color=C_DANGER, icon="🚫",
                      border_color="rgba(239,68,68,0.3)", delta="OFAC/EU/UN sanctioned")
        with s2:
            _kpi_card("Restricted Jurisdictions", str(restricted_ct), color=C_ORANGE, icon="⛔",
                      border_color="rgba(249,115,22,0.3)", delta="Sector sanctions apply")
        with s3:
            _kpi_card("Monitored Jurisdictions", str(monitored_ct), color=C_WARN, icon="👁",
                      border_color="rgba(245,158,11,0.3)", delta="Enhanced due diligence")
        with s4:
            _kpi_card("Active Regimes Tracked", str(total_regimes), color=C_ACCENT, icon="📊",
                      border_color="rgba(59,130,246,0.3)", delta="Multi-authority")

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        # Choropleth world map
        try:
            all_countries = _SANCTION_COUNTRIES + [
                {"iso": "USA", "name": "United States",   "status": "CLEAR", "note": "Sanctions issuing authority."},
                {"iso": "DEU", "name": "Germany",         "status": "CLEAR", "note": "EU member."},
                {"iso": "NLD", "name": "Netherlands",     "status": "CLEAR", "note": "Rotterdam hub."},
                {"iso": "SGP", "name": "Singapore",       "status": "CLEAR", "note": "Major transshipment hub."},
                {"iso": "JPN", "name": "Japan",           "status": "CLEAR", "note": "G7 sanctions-aligned."},
                {"iso": "GBR", "name": "United Kingdom",  "status": "CLEAR", "note": "OFSI sanctions authority."},
                {"iso": "AUS", "name": "Australia",       "status": "CLEAR", "note": "G7 coalition member."},
                {"iso": "GRC", "name": "Greece",          "status": "CLEAR", "note": "Piraeus — world's largest ship-owning nation."},
                {"iso": "PAN", "name": "Panama",          "status": "CLEAR", "note": "Largest ship registry."},
                {"iso": "ARE", "name": "UAE",             "status": "CLEAR", "note": "Jebel Ali major hub."},
                {"iso": "SAU", "name": "Saudi Arabia",    "status": "CLEAR", "note": "No restrictions."},
                {"iso": "IND", "name": "India",           "status": "CLEAR", "note": "Non-sanctioning. Russian oil receiver — monitor."},
                {"iso": "CHE", "name": "Switzerland",     "status": "CLEAR", "note": "Neutral; mirrors EU Russia measures."},
                {"iso": "KOR", "name": "South Korea",     "status": "CLEAR", "note": "G7-aligned."},
                {"iso": "BRA", "name": "Brazil",          "status": "CLEAR", "note": "No active trade sanctions."},
                {"iso": "EGY", "name": "Egypt",           "status": "CLEAR", "note": "Suez Canal authority."},
                {"iso": "MYS", "name": "Malaysia",        "status": "CLEAR", "note": "Full access. Transshipment hub."},
                {"iso": "IDN", "name": "Indonesia",       "status": "CLEAR", "note": "Malacca Strait corridor."},
            ]
            isos   = [c["iso"]    for c in all_countries]
            names  = [c["name"]   for c in all_countries]
            colors = [_STATUS_COLOR_MAP.get(c["status"], "#64748b") for c in all_countries]
            scores = [_STATUS_SCORE_MAP.get(c["status"], 0.0)       for c in all_countries]
            notes  = [c["note"]   for c in all_countries]
            statuses = [c["status"] for c in all_countries]

            color_scale = [
                [0.0, "#10b981"],
                [0.3, "#f59e0b"],
                [0.6, "#f97316"],
                [1.0, "#ef4444"],
            ]
            fig_map = go.Figure(go.Choropleth(
                locations=isos,
                z=scores,
                text=names,
                customdata=list(zip(statuses, notes)),
                colorscale=color_scale,
                zmin=0, zmax=1,
                marker_line_color="rgba(255,255,255,0.15)",
                marker_line_width=0.5,
                showscale=False,
                hovertemplate="<b>%{text}</b><br>Status: %{customdata[0]}<br>%{customdata[1]}<extra></extra>",
            ))
            fig_map.update_layout(
                geo=dict(
                    showframe=False,
                    showcoastlines=True,
                    coastlinecolor="rgba(255,255,255,0.12)",
                    showland=True, landcolor=C_CARD,
                    showocean=True, oceancolor=C_BG,
                    showlakes=False,
                    bgcolor="rgba(0,0,0,0)",
                    projection_type="natural earth",
                ),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                height=350,
                margin=dict(l=0, r=0, t=10, b=0),
            )
            st.plotly_chart(fig_map, use_container_width=True, config={"displayModeBar": False},
                            key="sanctions_world_map")
        except Exception as map_exc:
            logger.warning(f"Sanctions map error: {map_exc}")

        # Flagged entity cards
        _section_title("Active Sanctions Regimes — High Risk",
                        f"Showing CRITICAL and HIGH risk regimes from {len(ACTIVE_SANCTIONS)} tracked")
        critical_regimes = [s for s in ACTIVE_SANCTIONS if s.risk_level in ("CRITICAL", "HIGH")]
        cols_regime = st.columns(2, gap="medium")
        for idx, regime in enumerate(critical_regimes[:8]):
            try:
                col = cols_regime[idx % 2]
                impact_color = _STATUS_COLOR_MAP.get(regime.shipping_impact, C_TEXT2)
                risk_color   = C_DANGER if regime.risk_level == "CRITICAL" else C_ORANGE
                with col:
                    st.markdown(
                        f"<div style='background:{C_CARD}; border:1px solid {C_BORDER}; border-left:3px solid {risk_color};"
                        f" border-radius:10px; padding:14px 16px; margin-bottom:10px'>"
                        f"<div style='display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px'>"
                        f"<span style='font-size:0.85rem; font-weight:700; color:{C_TEXT}'>{regime.regime_name}</span>"
                        f"<div style='display:flex; gap:6px; flex-wrap:wrap'>"
                        f"{_badge(regime.risk_level, risk_color)}&nbsp;{_badge(regime.shipping_impact, impact_color)}"
                        f"</div></div>"
                        f"<div style='font-size:0.75rem; color:{C_TEXT2}; margin-bottom:10px; line-height:1.5'>"
                        f"{regime.scope[:180]}{'…' if len(regime.scope) > 180 else ''}</div>"
                        f"<div style='display:grid; grid-template-columns:1fr 1fr 1fr; gap:6px'>"
                        f"<div><span style='font-size:0.65rem; color:{C_TEXT3}'>Authority</span><br>"
                        f"<span style='font-size:0.78rem; font-weight:600; color:{C_PURPLE}'>{regime.sanctioning_authority}</span></div>"
                        f"<div><span style='font-size:0.65rem; color:{C_TEXT3}'>Max Penalty</span><br>"
                        f"<span style='font-size:0.78rem; color:{C_DANGER}'>${regime.penalty_per_violation_usd:,}</span></div>"
                        f"<div><span style='font-size:0.65rem; color:{C_TEXT3}'>2024 Actions</span><br>"
                        f"<span style='font-size:0.78rem; color:{C_WARN}'>{regime.enforcement_cases_2024}</span></div>"
                        f"</div></div>",
                        unsafe_allow_html=True,
                    )
            except Exception:
                continue

    except Exception as exc:
        logger.error(f"Sanctions monitor error: {exc}")
        st.warning("Sanctions screening monitor temporarily unavailable.")


# ---------------------------------------------------------------------------
# Section 6 — Port State Control Detention Risk Cards
# ---------------------------------------------------------------------------

def _render_psc_risk() -> None:
    try:
        risk_filter = st.select_slider(
            "Filter by detention risk level:",
            options=["ALL", "LOW", "MODERATE", "HIGH", "CRITICAL"],
            value="ALL",
            key="psc_risk_filter",
        )

        ports = _PSC_PORTS
        if risk_filter != "ALL":
            ports = [p for p in ports if p["risk_level"] == risk_filter]

        if not ports:
            st.info(f"No ports with {risk_filter} detention risk level.")
            return

        cols = st.columns(2, gap="medium")
        for idx, port in enumerate(ports):
            try:
                col = cols[idx % 2]
                risk_level = port["risk_level"]
                r_color = {
                    "CRITICAL": C_DANGER, "HIGH": C_ORANGE,
                    "MODERATE": C_WARN, "LOW": C_HIGH
                }.get(risk_level, C_TEXT2)

                detention_pct = port["detention_rate_pct"]
                bar_color = C_DANGER if detention_pct >= 8 else C_ORANGE if detention_pct >= 5 else C_WARN if detention_pct >= 3 else C_HIGH

                with col:
                    st.markdown(
                        f"<div style='background:{C_CARD}; border:1px solid {C_BORDER}; border-top:3px solid {r_color};"
                        f" border-radius:12px; padding:16px 18px; margin-bottom:10px'>"
                        f"<div style='display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px'>"
                        f"<div>"
                        f"<div style='font-size:0.90rem; font-weight:700; color:{C_TEXT}'>{port['port']}</div>"
                        f"<div style='font-size:0.72rem; color:{C_TEXT3}'>{port['country']} · {port['psc_regime']}</div>"
                        f"</div>"
                        f"{_badge(risk_level, r_color)}"
                        f"</div>"
                        f"<div style='margin:10px 0'>"
                        f"{_progress_bar(min(detention_pct * 8, 100), bar_color, height=10, label=f'Detention Rate: {detention_pct:.1f}%')}"
                        f"</div>"
                        f"<div style='display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:10px'>"
                        f"<div style='background:rgba(255,255,255,0.04); border-radius:6px; padding:8px'>"
                        f"<div style='font-size:0.65rem; color:{C_TEXT3}'>Inspections 2024</div>"
                        f"<div style='font-size:0.88rem; font-weight:700; color:{C_TEXT}'>{port['inspections_2024']:,}</div>"
                        f"</div>"
                        f"<div style='background:rgba(255,255,255,0.04); border-radius:6px; padding:8px'>"
                        f"<div style='font-size:0.65rem; color:{C_TEXT3}'>Avg Deficiencies</div>"
                        f"<div style='font-size:0.88rem; font-weight:700; color:{bar_color}'>{port['deficiencies_avg']}</div>"
                        f"</div>"
                        f"</div>"
                        f"<div style='font-size:0.75rem; color:{C_TEXT2}; background:rgba(255,255,255,0.03); border-radius:6px; padding:6px 10px; margin-bottom:8px'>"
                        f"<b style='color:{C_TEXT3}'>Top deficiency:</b> {port['top_deficiency']}</div>"
                        f"<div style='font-size:0.73rem; color:{C_TEXT3}'>{port['note']}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
            except Exception:
                continue

    except Exception as exc:
        logger.error(f"PSC risk error: {exc}")
        st.warning("Port State Control risk cards temporarily unavailable.")


# ---------------------------------------------------------------------------
# Section 7 — Compliance Deadline Calendar
# ---------------------------------------------------------------------------

def _render_deadline_calendar() -> None:
    try:
        # Group by month
        from collections import defaultdict
        by_month: dict[str, list] = defaultdict(list)
        for ev in _DEADLINE_EVENTS:
            mk = ev["date"].strftime("%B %Y")
            by_month[mk].append(ev)

        cat_colors = {
            "EU ETS":  C_TEAL,
            "Document": C_ORANGE,
            "CII":     C_ACCENT,
            "Class":   C_PURPLE,
            "ISM":     C_WARN,
            "Manning": C_HIGH,
        }

        urgency_colors = {
            "CRITICAL": C_DANGER,
            "HIGH":     C_ORANGE,
            "MODERATE": C_WARN,
            "LOW":      C_HIGH,
        }

        for month_label, events in sorted(by_month.items(), key=lambda x: datetime.datetime.strptime(x[0], "%B %Y")):
            st.markdown(
                f"<div style='background:rgba(255,255,255,0.03); border-radius:8px; padding:8px 14px; margin:12px 0 6px; display:inline-block'>"
                f"<span style='font-size:0.85rem; font-weight:700; color:{C_TEXT}'>{month_label}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            for ev in sorted(events, key=lambda x: x["date"]):
                try:
                    days = _days_until(ev["date"])
                    u_color = urgency_colors.get(ev.get("urgency", "MODERATE"), C_WARN)
                    c_color = cat_colors.get(ev.get("category", ""), C_ACCENT)

                    if days < 0:
                        days_str  = f"Overdue by {abs(days)}d"
                        d_color   = C_DANGER
                    elif days == 0:
                        days_str  = "Due TODAY"
                        d_color   = C_DANGER
                    elif days <= 30:
                        days_str  = f"In {days} days"
                        d_color   = C_DANGER
                    elif days <= 90:
                        days_str  = f"In {days} days"
                        d_color   = C_ORANGE
                    else:
                        days_str  = f"In {days} days"
                        d_color   = C_TEXT2

                    st.markdown(
                        f"<div style='background:{C_CARD}; border:1px solid {C_BORDER}; border-left:3px solid {u_color};"
                        f" border-radius:8px; padding:10px 16px; margin-bottom:6px;"
                        f" display:flex; align-items:center; gap:12px; flex-wrap:wrap'>"
                        f"<div style='min-width:90px; text-align:center; background:rgba(255,255,255,0.04);"
                        f" border-radius:6px; padding:6px 10px'>"
                        f"<div style='font-size:0.68rem; color:{C_TEXT3}'>{ev['date'].strftime('%b')}</div>"
                        f"<div style='font-size:1.2rem; font-weight:800; color:{u_color}'>{ev['date'].day:02d}</div>"
                        f"<div style='font-size:0.68rem; color:{C_TEXT3}'>{ev['date'].year}</div>"
                        f"</div>"
                        f"<div style='flex:1; min-width:200px'>"
                        f"<div style='display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:4px'>"
                        f"<span style='font-size:0.85rem; font-weight:700; color:{C_TEXT}'>{ev['title']}</span>"
                        f"{_badge(ev.get('category',''), c_color)}"
                        f"</div>"
                        f"<div style='font-size:0.75rem; color:{C_TEXT2}'>{ev['description']}</div>"
                        f"</div>"
                        f"<div style='font-size:0.80rem; font-weight:700; color:{d_color}; min-width:100px; text-align:right'>{days_str}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                except Exception:
                    continue

    except Exception as exc:
        logger.error(f"Deadline calendar error: {exc}")
        st.warning("Compliance deadline calendar temporarily unavailable.")


# ---------------------------------------------------------------------------
# Section 8 — Flag State Compliance Ranking
# ---------------------------------------------------------------------------

def _render_flag_ranking() -> None:
    try:
        sort_by = st.radio(
            "Sort by:",
            ["Compliance Score", "Fleet Size (GT)", "Detention Rate"],
            horizontal=True,
            key="flag_sort_by",
        )

        flags = list(_FLAG_STATES)
        if sort_by == "Compliance Score":
            flags.sort(key=lambda x: x["compliance_score"], reverse=True)
        elif sort_by == "Fleet Size (GT)":
            flags.sort(key=lambda x: x["fleet_gt"], reverse=True)
        else:
            flags.sort(key=lambda x: x["psc_detention_rate"])

        # Header
        st.markdown(
            f"<div style='display:grid; grid-template-columns:2fr 1fr 1fr 1fr 1fr 1fr; gap:8px;"
            f" padding:8px 14px; margin-bottom:4px'>"
            f"<span style='font-size:0.68rem; color:{C_TEXT3}; text-transform:uppercase'>Flag State</span>"
            f"<span style='font-size:0.68rem; color:{C_TEXT3}; text-transform:uppercase'>Registry</span>"
            f"<span style='font-size:0.68rem; color:{C_TEXT3}; text-transform:uppercase'>Fleet GT</span>"
            f"<span style='font-size:0.68rem; color:{C_TEXT3}; text-transform:uppercase'>Detention %</span>"
            f"<span style='font-size:0.68rem; color:{C_TEXT3}; text-transform:uppercase'>IMO Audit</span>"
            f"<span style='font-size:0.68rem; color:{C_TEXT3}; text-transform:uppercase'>Score</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

        for rank, flag in enumerate(flags, 1):
            try:
                score      = flag["compliance_score"]
                s_color    = C_HIGH if score >= 90 else C_WARN if score >= 75 else C_DANGER
                det_color  = C_HIGH if flag["psc_detention_rate"] < 2.5 else C_WARN if flag["psc_detention_rate"] < 6 else C_DANGER
                wl_badge   = _badge("WHITE LIST", C_HIGH) if flag["white_list"] else _badge("GREY/BLACK", C_DANGER)
                audit_color = C_HIGH if "Passed" in flag["imo_audit"] else C_WARN if "Due" in flag["imo_audit"] else C_DANGER
                gt_str     = f"{flag['fleet_gt'] / 1_000_000:.0f}M GT"

                st.markdown(
                    f"<div style='background:{C_CARD}; border:1px solid {C_BORDER}; border-radius:8px;"
                    f" padding:10px 14px; margin-bottom:5px; display:grid;"
                    f" grid-template-columns:2fr 1fr 1fr 1fr 1fr 1fr; gap:8px; align-items:center'>"
                    f"<div style='display:flex; align-items:center; gap:8px'>"
                    f"<span style='font-size:0.70rem; color:{C_TEXT3}; font-weight:700; min-width:20px'>#{rank}</span>"
                    f"<span style='font-size:0.82rem; font-weight:600; color:{C_TEXT}'>{flag['flag']}</span>"
                    f"</div>"
                    f"<div style='font-size:0.75rem; color:{C_TEXT2}'>{flag['registry_type']}</div>"
                    f"<div style='font-size:0.78rem; color:{C_TEXT}'>{gt_str}</div>"
                    f"<div style='font-size:0.80rem; font-weight:600; color:{det_color}'>{flag['psc_detention_rate']:.1f}%</div>"
                    f"<div style='font-size:0.72rem; color:{audit_color}'>{flag['imo_audit']}</div>"
                    f"<div>"
                    f"<div style='display:flex; align-items:center; gap:8px'>"
                    f"<span style='font-size:0.88rem; font-weight:800; color:{s_color}'>{score}</span>"
                    f"{_badge('WL', C_HIGH) if flag['white_list'] else _badge('GL', C_DANGER)}"
                    f"</div>"
                    f"{_progress_bar(score, s_color, height=5)}"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            except Exception:
                continue

    except Exception as exc:
        logger.error(f"Flag ranking error: {exc}")
        st.warning("Flag state compliance ranking temporarily unavailable.")


# ---------------------------------------------------------------------------
# Section 9 — Documentation Status Tracker
# ---------------------------------------------------------------------------

def _render_doc_tracker() -> None:
    try:
        cat_filter = st.multiselect(
            "Filter by category:",
            options=sorted(set(d["category"] for d in _DOCUMENTS)),
            default=[],
            key="doc_cat_filter",
            placeholder="All categories",
        )

        docs = _DOCUMENTS
        if cat_filter:
            docs = [d for d in docs if d["category"] in cat_filter]

        # Sort by urgency (soonest expiry first)
        docs = sorted(docs, key=lambda d: _days_until(d["expires"]))

        # Summary row
        expired_ct  = sum(1 for d in _DOCUMENTS if _days_until(d["expires"]) < 0)
        critical_ct = sum(1 for d in _DOCUMENTS if 0 <= _days_until(d["expires"]) <= 30)
        warn_ct     = sum(1 for d in _DOCUMENTS if 30 < _days_until(d["expires"]) <= 90)
        ok_ct       = sum(1 for d in _DOCUMENTS if _days_until(d["expires"]) > 90)

        ds1, ds2, ds3, ds4 = st.columns(4)
        with ds1:
            _kpi_card("Expired",         str(expired_ct),  color=C_DANGER, icon="❌", border_color="rgba(239,68,68,0.3)")
        with ds2:
            _kpi_card("Critical (≤30d)", str(critical_ct), color=C_DANGER, icon="🚨", border_color="rgba(239,68,68,0.3)")
        with ds3:
            _kpi_card("Warning (≤90d)",  str(warn_ct),     color=C_WARN,   icon="⚠️", border_color="rgba(245,158,11,0.3)")
        with ds4:
            _kpi_card("Valid",           str(ok_ct),       color=C_HIGH,   icon="✅", border_color="rgba(16,185,129,0.3)")

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        cat_colors = {
            "Safety": C_HIGH, "ISM": C_ACCENT, "Emissions": C_TEAL,
            "Structural": C_PURPLE, "Environmental": C_HIGH, "Security": C_ORANGE,
            "Manning": C_WARN, "Carbon": C_TEAL, "Insurance": C_ACCENT,
        }

        for doc in docs:
            try:
                days     = _days_until(doc["expires"])
                d_color  = _urgency_color(days)
                c_color  = cat_colors.get(doc["category"], C_ACCENT)

                if days < 0:
                    status_str = f"EXPIRED {abs(days)}d ago"
                    status_badge = _badge("EXPIRED", C_DANGER)
                elif days <= 30:
                    status_str = f"EXPIRES IN {days}d"
                    status_badge = _badge("CRITICAL", C_DANGER)
                elif days <= 90:
                    status_str = f"Expires in {days}d"
                    status_badge = _badge("WARNING", C_ORANGE)
                else:
                    status_str = f"Valid — {days}d remaining"
                    status_badge = _badge("VALID", C_HIGH)

                renewal_due = doc["expires"] - datetime.timedelta(days=doc["renewal_lead_days"])
                renewal_days = _days_until(renewal_due)
                renewal_str = f"Renewal action due: {renewal_due.strftime('%d %b %Y')}"
                if renewal_days <= 0:
                    renewal_str += f" (overdue {abs(renewal_days)}d)"

                st.markdown(
                    f"<div style='background:{C_CARD}; border:1px solid {C_BORDER}; border-left:3px solid {d_color};"
                    f" border-radius:10px; padding:12px 18px; margin-bottom:7px'>"
                    f"<div style='display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:6px; margin-bottom:6px'>"
                    f"<div>"
                    f"<span style='font-size:0.85rem; font-weight:700; color:{C_TEXT}'>{doc['doc']}</span>&nbsp;"
                    f"{_badge(doc['category'], c_color)}"
                    f"</div>"
                    f"<div style='display:flex; gap:8px; align-items:center'>"
                    f"{status_badge}"
                    f"<span style='font-size:0.80rem; font-weight:700; color:{d_color}'>{status_str}</span>"
                    f"</div>"
                    f"</div>"
                    f"<div style='display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; margin-bottom:6px'>"
                    f"<div><span style='font-size:0.65rem; color:{C_TEXT3}'>Vessel</span><br>"
                    f"<span style='font-size:0.78rem; color:{C_TEXT}'>{doc['vessel']}</span></div>"
                    f"<div><span style='font-size:0.65rem; color:{C_TEXT3}'>Authority</span><br>"
                    f"<span style='font-size:0.78rem; color:{C_TEXT}'>{doc['authority']}</span></div>"
                    f"<div><span style='font-size:0.65rem; color:{C_TEXT3}'>Expires</span><br>"
                    f"<span style='font-size:0.78rem; color:{d_color}; font-weight:600'>{doc['expires'].strftime('%d %b %Y')}</span></div>"
                    f"</div>"
                    f"<div style='font-size:0.72rem; color:{C_TEXT3}'>{renewal_str}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            except Exception:
                continue

    except Exception as exc:
        logger.error(f"Doc tracker error: {exc}")
        st.warning("Documentation status tracker temporarily unavailable.")


# ---------------------------------------------------------------------------
# Section 10 — Regulatory Change Feed
# ---------------------------------------------------------------------------

def _render_reg_change_feed() -> None:
    try:
        impact_filter = st.radio(
            "Filter by impact level:",
            ["All", "CRITICAL", "HIGH", "MODERATE"],
            horizontal=True,
            key="reg_feed_filter",
        )

        regs = _REG_CHANGES
        if impact_filter != "All":
            regs = [r for r in regs if r["impact"] == impact_filter]

        impact_colors = {
            "CRITICAL": C_DANGER,
            "HIGH":     C_ORANGE,
            "MODERATE": C_WARN,
            "LOW":      C_HIGH,
        }
        status_colors = {
            "IN_FORCE": C_HIGH,
            "UPCOMING": C_ACCENT,
        }

        for reg in sorted(regs, key=lambda x: (x["status"] == "IN_FORCE", x["effective"]), reverse=True):
            try:
                i_color = impact_colors.get(reg["impact"], C_WARN)
                s_color = status_colors.get(reg["status"], C_TEXT2)
                cost_str = f"${reg['cost_impact_usd_m']:.1f}M" if reg["cost_impact_usd_m"] else "TBD"
                days = _days_until(reg["effective"])
                days_str = (
                    f"In force {abs(days)}d" if days < 0
                    else f"In force TODAY" if days == 0
                    else f"Effective in {days}d"
                )

                st.markdown(
                    f"<div style='background:{C_CARD}; border:1px solid {C_BORDER}; border-left:4px solid {i_color};"
                    f" border-radius:10px; padding:16px 20px; margin-bottom:10px'>"
                    f"<div style='display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px; margin-bottom:8px'>"
                    f"<div style='flex:1; min-width:200px'>"
                    f"<div style='display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:4px'>"
                    f"<span style='font-size:0.92rem; font-weight:700; color:{C_TEXT}'>{reg['regulation']}</span>"
                    f"{_badge(reg['status'].replace('_', ' '), s_color)}"
                    f"&nbsp;{_badge(reg['impact'] + ' IMPACT', i_color)}"
                    f"</div>"
                    f"<div style='font-size:0.72rem; color:{C_TEXT3}'>{reg['authority']} · {reg['effective'].strftime('%d %b %Y')} · {days_str}</div>"
                    f"</div>"
                    f"<div style='text-align:right; min-width:100px'>"
                    f"<div style='font-size:0.68rem; color:{C_TEXT3}; margin-bottom:2px'>Est. Cost Impact</div>"
                    f"<div style='font-size:1.1rem; font-weight:800; color:{i_color}'>{cost_str}</div>"
                    f"</div>"
                    f"</div>"
                    f"<div style='font-size:0.78rem; color:{C_TEXT2}; margin-bottom:10px; line-height:1.55'>{reg['description']}</div>"
                    f"<div style='font-size:0.75rem; color:{C_TEXT3}'><b style='color:{C_TEXT2}'>Vessels affected:</b> {reg['vessels_affected']}</div>"
                    f"<div style='margin-top:10px; background:rgba(255,255,255,0.04); border-radius:6px; padding:8px 12px'>"
                    f"<span style='font-size:0.68rem; text-transform:uppercase; font-weight:700; color:{i_color}'>Action Required</span>"
                    f"<div style='font-size:0.78rem; color:{C_TEXT}; margin-top:3px'>{reg['action_required']}</div>"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            except Exception:
                continue

    except Exception as exc:
        logger.error(f"Regulatory change feed error: {exc}")
        st.warning("Regulatory change feed temporarily unavailable.")


# ---------------------------------------------------------------------------
# Main render entry point — PRESERVED EXACT SIGNATURE
# ---------------------------------------------------------------------------

def render(route_results, port_results, macro_data) -> None:
    """Render the Maritime Compliance Command Center tab.

    Parameters
    ----------
    route_results : list[RouteOpportunity]
        Current route opportunity objects from the optimizer.
    port_results : list[PortDemandResult]
        Current port demand results.
    macro_data : dict
        Global macro indicators dict.
    """
    logger.info("Rendering Maritime Compliance Command Center tab")

    # ── Page header ──────────────────────────────────────────────────────────
    try:
        st.markdown(
            f"<div style='margin-bottom:4px'>"
            f"<h2 style='font-size:1.6rem; font-weight:800; color:{C_TEXT}; margin:0; letter-spacing:-0.01em'>"
            f"Maritime Compliance Command Center</h2>"
            f"<div style='font-size:0.80rem; color:{C_TEXT3}; margin-top:4px'>"
            f"Regulatory intelligence · Sanctions screening · CII tracking · EU ETS · PSC risk · Documentation"
            f"</div></div>",
            unsafe_allow_html=True,
        )
    except Exception:
        st.header("Maritime Compliance Command Center")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 1 — Compliance Status Hero Dashboard
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Compliance Status Overview",
            "Composite fleet compliance score, violation counts, active regulations, and upcoming deadlines",
        )
        _render_hero_dashboard()
    except Exception as exc:
        logger.error(f"Section 1 error: {exc}")
        st.error("Compliance status overview unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 2 — IMO Regulation Tracker
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "IMO Regulation Tracker — 2020/2023/2025",
            f"Tracking {len(_IMO_REGULATIONS)} IMO / EU regulations with fleet compliance rates, penalties, and status badges",
        )
        _render_imo_tracker()
    except Exception as exc:
        logger.error(f"Section 2 error: {exc}")
        st.error("IMO regulation tracker unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 3 — CII Rating Cards
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "CII Rating Cards by Vessel Type",
            "Carbon Intensity Indicator (A–E) per vessel category with trend sparklines, attained vs required CII, and year-on-year change",
        )
        _render_cii_cards()
    except Exception as exc:
        logger.error(f"Section 3 error: {exc}")
        st.error("CII rating cards unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 4 — EU ETS Dashboard
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "EU ETS Compliance Dashboard",
            f"European Emissions Trading System — carbon allowance tracker, phase-in timeline, and route exposure. EUA price: €{_EU_ETS['eua_price_eur']:.2f}/t",
        )
        _render_eu_ets()
    except Exception as exc:
        logger.error(f"Section 4 error: {exc}")
        st.error("EU ETS dashboard unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 5 — Sanctions Screening Monitor
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Sanctions Screening Monitor",
            f"{len(ACTIVE_SANCTIONS)} active sanction regimes tracked across OFAC, EU, UN, and UK authorities. Jurisdiction risk map and regime detail cards.",
        )
        _render_sanctions_monitor()
    except Exception as exc:
        logger.error(f"Section 5 error: {exc}")
        st.error("Sanctions screening monitor unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 6 — Port State Control Detention Risk Cards
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Port State Control Detention Risk",
            f"Detention rates, deficiency profiles, and inspection volumes for {len(_PSC_PORTS)} key ports across Tokyo, Paris, US CG, Viña del Mar, Indian Ocean, and Riyadh MOU",
        )
        _render_psc_risk()
    except Exception as exc:
        logger.error(f"Section 6 error: {exc}")
        st.error("Port State Control risk cards unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 7 — Compliance Deadline Calendar
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Compliance Deadline Calendar",
            f"{len(_DEADLINE_EVENTS)} upcoming regulatory deadlines — certificates, ETS obligations, survey windows, and reporting requirements",
        )
        _render_deadline_calendar()
    except Exception as exc:
        logger.error(f"Section 7 error: {exc}")
        st.error("Compliance deadline calendar unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 8 — Flag State Compliance Ranking
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Flag State Compliance Ranking",
            f"{len(_FLAG_STATES)} flag registries ranked by composite compliance score — PSC detention rate, IMO audit status, Paris/Tokyo MOU white/grey/black list classification",
        )
        _render_flag_ranking()
    except Exception as exc:
        logger.error(f"Section 8 error: {exc}")
        st.error("Flag state compliance ranking unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 9 — Documentation Status Tracker
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Documentation Status Tracker",
            f"{len(_DOCUMENTS)} vessel certificates tracked — sorted by urgency with expiry alerts, renewal lead times, and category filtering",
        )
        _render_doc_tracker()
    except Exception as exc:
        logger.error(f"Section 9 error: {exc}")
        st.error("Documentation status tracker unavailable.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # Section 10 — Regulatory Change Feed
    # ══════════════════════════════════════════════════════════════════════════
    try:
        _section_title(
            "Regulatory Change Feed",
            f"{len(_REG_CHANGES)} upcoming and in-force regulations with impact assessments, action requirements, and cost estimates",
        )
        _render_reg_change_feed()
    except Exception as exc:
        logger.error(f"Section 10 error: {exc}")
        st.error("Regulatory change feed unavailable.")

    st.markdown("<div style='height:30px'></div>", unsafe_allow_html=True)
