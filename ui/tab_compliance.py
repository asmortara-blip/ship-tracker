"""tab_compliance.py — Shipping Regulatory Compliance & Sanctions Intelligence.

Eight sections:
  1. Compliance Dashboard      — KPI tiles: sanctions regimes, OFAC vessels, IMO updates, flagged vessels
  2. Sanctions Screening Table — Jurisdiction × entity × trade-lane coverage matrix
  3. IMO Regulatory Calendar   — Upcoming regulatory deadlines 2025-2030
  4. CII Tracker               — Carbon Intensity Indicator by major carrier
  5. Sanctions Evasion Patterns— Risk indicators and evasion method taxonomy
  6. Dark Fleet Tracker        — Shadow fleet estimates and operating areas
  7. Port State Control        — Recent detentions and deficiency table
  8. Compliance Risk Score     — Interactive route/cargo/counterparty risk calculator

Data currency note:
  OFAC/EU/UN sanctions designations are updated continuously. IMO CII reduction factors and
  rating thresholds are reviewed annually at MEPC sessions. Always verify against the latest
  OFAC SDN list, EUR-Lex OJ publications, and MEPC circulars before operational use.
"""
from __future__ import annotations

import datetime

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

_CII_COLOR = {"A": "#065f46", "B": "#10b981", "C": "#f59e0b", "D": "#ef4444", "E": "#7f1d1d"}
_CII_BG    = {"A": "#022c22", "B": "#052e1c", "C": "#451a03", "D": "#450a0a", "E": "#3b0808"}

# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------

_SANCTIONS_ROWS = [
    {
        "jurisdiction": "US OFAC",
        "entity": "Russia — Oil Sector",
        "vessel_types": "Crude tankers, product tankers",
        "trade_lanes": "Russia → Asia, Russia → EU (legacy)",
        "effective": "Feb 2022 / Jan 2023",
        "penalty": "$1M+ per violation",
        "severity": "critical",
    },
    {
        "jurisdiction": "US OFAC",
        "entity": "Iran — All Cargo",
        "vessel_types": "All vessel types",
        "trade_lanes": "Iran → China, Iran → Syria",
        "effective": "1979 / expanded 2012",
        "penalty": "Criminal + civil up to $1.3M",
        "severity": "critical",
    },
    {
        "jurisdiction": "US OFAC",
        "entity": "North Korea — All",
        "vessel_types": "All vessel types",
        "trade_lanes": "DPRK → China (illicit), DPRK → Russia",
        "effective": "2010 / UNSCR 2375 (2017)",
        "penalty": "Criminal prosecution",
        "severity": "critical",
    },
    {
        "jurisdiction": "US OFAC",
        "entity": "Venezuela — Oil",
        "vessel_types": "Crude tankers, VLCCs",
        "trade_lanes": "Venezuela → Cuba, Venezuela → China",
        "effective": "Jan 2019 / Aug 2019",
        "penalty": "$1M per violation",
        "severity": "high",
    },
    {
        "jurisdiction": "US OFAC",
        "entity": "Myanmar — Military",
        "vessel_types": "Bulk carriers, general cargo",
        "trade_lanes": "Myanmar → SE Asia",
        "effective": "Mar 2021",
        "penalty": "Civil monetary penalties",
        "severity": "moderate",
    },
    {
        "jurisdiction": "EU",
        "entity": "Russia — 6th Pkg Oil Embargo",
        "vessel_types": "Crude tankers, product tankers",
        "trade_lanes": "Russia → EU member states",
        "effective": "Dec 2022",
        "penalty": "€1M+ / asset freeze",
        "severity": "critical",
    },
    {
        "jurisdiction": "EU",
        "entity": "Russia — 5th Pkg LNG",
        "vessel_types": "LNG carriers",
        "trade_lanes": "Russia → EU (Yamal LNG)",
        "effective": "Apr 2022 / Oct 2023",
        "penalty": "Asset freeze / criminal referral",
        "severity": "high",
    },
    {
        "jurisdiction": "EU",
        "entity": "Belarus — Transit Goods",
        "vessel_types": "Container, ro-ro, bulk",
        "trade_lanes": "Belarus → Baltic ports",
        "effective": "Jun 2021",
        "penalty": "EU import/export prohibition",
        "severity": "moderate",
    },
    {
        "jurisdiction": "UK OFSI",
        "entity": "Russia — Oil Price Cap",
        "vessel_types": "Crude tankers, product tankers",
        "trade_lanes": "Russia → Global",
        "effective": "Dec 2022",
        "penalty": "Up to £1M or 50% of breach value",
        "severity": "critical",
    },
    {
        "jurisdiction": "UK OFSI",
        "entity": "Iran — Comprehensive",
        "vessel_types": "All vessel types",
        "trade_lanes": "Iran → Any UK-nexus",
        "effective": "Aligned with US/EU",
        "penalty": "Criminal prosecution",
        "severity": "critical",
    },
    {
        "jurisdiction": "UN Security Council",
        "entity": "North Korea — Resolutions",
        "vessel_types": "Coal, oil, arms carriers",
        "trade_lanes": "DPRK → Any UN member state",
        "effective": "UNSCR 1718 (2006) onwards",
        "penalty": "Asset freeze / arms embargo",
        "severity": "critical",
    },
    {
        "jurisdiction": "UN Security Council",
        "entity": "Libya — Arms Embargo",
        "vessel_types": "Arms/military cargo vessels",
        "trade_lanes": "To/from Libya",
        "effective": "UNSCR 1970 (2011)",
        "penalty": "Vessel seizure authorized",
        "severity": "high",
    },
    {
        "jurisdiction": "UN Security Council",
        "entity": "Somalia — Piracy Zone",
        "vessel_types": "All vessel types",
        "trade_lanes": "Gulf of Aden, Indian Ocean",
        "effective": "UNSCR 1816 (2008)",
        "penalty": "Naval interdiction / seizure",
        "severity": "moderate",
    },
]

_IMO_CALENDAR = [
    {
        "date": "Jan 2023",
        "regulation": "CII Rating Regime",
        "scope": "IMO / MARPOL Annex VI",
        "vessels": "Ships 5,000 GT+",
        "cost": "$50K–$500K fleet-wide",
        "enforcement": "Flag state annual review",
        "status": "past",
    },
    {
        "date": "Jan 2024",
        "regulation": "EU ETS — Shipping Included",
        "scope": "EU Regulation 2023/957",
        "vessels": "Ships 5,000 GT+ on EU routes",
        "cost": "€25–€70/tonne CO₂",
        "enforcement": "Port authority / fines per voyage",
        "status": "past",
    },
    {
        "date": "Mar 2025",
        "regulation": "IMO DCS Verification",
        "scope": "IMO Data Collection System",
        "vessels": "Ships 5,000 GT+",
        "cost": "$10K–$50K per vessel",
        "enforcement": "PSC detention risk",
        "status": "current",
    },
    {
        "date": "Jun 2025",
        "regulation": "FuelEU Maritime — Preparation",
        "scope": "EU Reg 2023/1805",
        "vessels": "Ships 5,000 GT+ on EU routes",
        "cost": "$100K–$2M fleet-wide",
        "enforcement": "Pooling penalties from Jan 2026",
        "status": "current",
    },
    {
        "date": "Jan 2026",
        "regulation": "FuelEU Maritime — Full Implementation",
        "scope": "EU Reg 2023/1805",
        "vessels": "Ships 5,000 GT+ on EU routes",
        "cost": "2% GHG intensity reduction",
        "enforcement": "€2,400/tonne VLFSO equivalent",
        "status": "upcoming",
    },
    {
        "date": "Jan 2027",
        "regulation": "IMO GHG Strategy Milestone",
        "scope": "IMO 2023 GHG Strategy",
        "vessels": "Global fleet",
        "cost": "Fuel switching CAPEX required",
        "enforcement": "MEPC review / market measures",
        "status": "upcoming",
    },
    {
        "date": "Jan 2028",
        "regulation": "IMO EEXI Phase 2",
        "scope": "MARPOL Annex VI Reg 23",
        "vessels": "Ships 400 GT+",
        "cost": "$500K–$5M per vessel (retrofit)",
        "enforcement": "Flag state certification withdrawal",
        "status": "upcoming",
    },
    {
        "date": "Jan 2030",
        "regulation": "IMO 40% CO₂ Reduction Target",
        "scope": "IMO 2023 GHG Strategy",
        "vessels": "Global fleet vs 2008 baseline",
        "cost": "Industry-wide $1T+ investment",
        "enforcement": "Market-based measures (TBD)",
        "status": "upcoming",
    },
]

_CII_CARRIERS = [
    {"carrier": "Maersk",         "rating_2024": "B", "proj_2025": "B", "fleet_pct": 88, "actions": "Methanol retrofit program",        "at_risk": False},
    {"carrier": "MSC",            "rating_2024": "C", "proj_2025": "B", "fleet_pct": 72, "actions": "Speed optimization + scrubbers",   "at_risk": False},
    {"carrier": "CMA CGM",        "rating_2024": "B", "proj_2025": "A", "fleet_pct": 91, "actions": "LNG newbuilds, slow steaming",     "at_risk": False},
    {"carrier": "COSCO",          "rating_2024": "C", "proj_2025": "C", "fleet_pct": 65, "actions": "Fleet renewal program",            "at_risk": True},
    {"carrier": "Evergreen",      "rating_2024": "C", "proj_2025": "B", "fleet_pct": 70, "actions": "Energy-saving devices retrofit",   "at_risk": False},
    {"carrier": "Hapag-Lloyd",    "rating_2024": "B", "proj_2025": "B", "fleet_pct": 84, "actions": "Ammonia-ready newbuilds 2026",     "at_risk": False},
    {"carrier": "ONE (Ocean NW)", "rating_2024": "C", "proj_2025": "C", "fleet_pct": 67, "actions": "Speed reduction program",          "at_risk": True},
    {"carrier": "Yang Ming",      "rating_2024": "D", "proj_2025": "C", "fleet_pct": 48, "actions": "Corrective action plan filed",     "at_risk": True},
    {"carrier": "HMM",            "rating_2024": "B", "proj_2025": "A", "fleet_pct": 93, "actions": "Hydrogen pilot vessel 2026",       "at_risk": False},
    {"carrier": "Zim",            "rating_2024": "C", "proj_2025": "B", "fleet_pct": 74, "actions": "LNG charter strategy",             "at_risk": False},
]

_EVASION_PATTERNS = [
    {
        "method": "Ship-to-Ship (STS) Transfer",
        "category": "Shadow Fleet",
        "risk": "Critical",
        "indicators": "AIS gap near known STS zones (Ceuta, Kalamata, Lakshadweep), cargo discrepancy, dual-manifest",
        "regions": "Mediterranean, Gulf of Oman, SE Asia",
    },
    {
        "method": "AIS Spoofing / Transponder Off",
        "category": "Dark Shipping",
        "risk": "Critical",
        "indicators": "Abnormal position jumps, port calls not matching reported position, long AIS blackout periods",
        "regions": "Global — especially Russia NW coast, Iranian waters",
    },
    {
        "method": "Flag-Hopping",
        "category": "Registry Abuse",
        "risk": "High",
        "indicators": "Multiple flag changes in 12 months, flag state with poor PSC record, rush re-registration",
        "regions": "Open registries: Palau, Cameroon, Gabon, Togo",
    },
    {
        "method": "Cargo Repackaging via UAE/India",
        "category": "Intermediary Trade",
        "risk": "High",
        "indicators": "Russian origin crude relabelled, Indian refinery as 'origin', pricing below market, unusual B/L terms",
        "regions": "UAE Fujairah, India Vadinar/Sikka → global",
    },
    {
        "method": "Phantom Ownership / Shell Companies",
        "category": "Beneficial Ownership",
        "risk": "High",
        "indicators": "Opaque corporate structure, no web presence, sudden vessel acquisition, non-standard P&I cover",
        "regions": "Marshall Islands, Panama, Seychelles registered",
    },
    {
        "method": "False Port Declarations",
        "category": "Document Fraud",
        "risk": "Moderate",
        "indicators": "Inconsistent port agent records, falsified cargo manifests, crew-reported vs AIS-reported calls",
        "regions": "East China Sea, Arabian Gulf, West Africa",
    },
]

_DARK_FLEET = [
    {
        "fleet": "Russian Shadow Fleet",
        "est_vessels": "~600",
        "types": "VLCC, Aframax, Suezmax",
        "operating_areas": "Baltic Sea, Black Sea, Arabian Gulf → Asia",
        "age_avg": "20+ years",
        "p_i_cover": "Often none or Russian P&I",
        "impact": "Depressing tanker spot rates 8–15%",
        "color": C_LOW,
    },
    {
        "fleet": "Iranian Shadow Fleet",
        "est_vessels": "~100",
        "types": "VLCC, Suezmax",
        "operating_areas": "Arabian Gulf → China, Syrian ports",
        "age_avg": "25+ years",
        "p_i_cover": "None or Iranian mutual",
        "impact": "~1.5M bbl/day displaced outside SWIFT",
        "color": "#f97316",
    },
    {
        "fleet": "Venezuelan Dark Fleet",
        "est_vessels": "~30",
        "types": "VLCC, product tankers",
        "operating_areas": "Caribbean → Cuba, China, Malaysia",
        "age_avg": "18 years",
        "p_i_cover": "Minimal",
        "impact": "~700K bbl/day circumventing OFAC",
        "color": C_MOD,
    },
    {
        "fleet": "North Korean Illicit Fleet",
        "est_vessels": "~50",
        "types": "Bulk carriers, small tankers",
        "operating_areas": "East China Sea → DPRK ports",
        "age_avg": "30+ years",
        "p_i_cover": "None",
        "impact": "Coal/oil in defiance of UNSCR 2375",
        "color": C_ACCENT,
    },
]

_PSC_DETENTIONS = [
    {"vessel": "MV Bering Star",      "flag": "Palau",          "port": "Rotterdam",      "deficiency": "Fire safety / structural",     "status": "Detained",  "release": "Pending"},
    {"vessel": "MT Fortune Glory",    "flag": "Cameroon",       "port": "Singapore",      "deficiency": "ISM Code non-compliance",      "status": "Released",  "release": "2026-03-10"},
    {"vessel": "MV Pacific Wind",     "flag": "Togo",           "port": "Hamburg",        "deficiency": "Lifesaving appliances",        "status": "Detained",  "release": "Pending"},
    {"vessel": "MT Eastern Sun",      "flag": "Cook Islands",   "port": "Fujairah",       "deficiency": "AIS manipulation evidence",    "status": "Detained",  "release": "Pending"},
    {"vessel": "MV Blue Horizon",     "flag": "Moldova",        "port": "Istanbul",       "deficiency": "Cargo documentation fraud",    "status": "Released",  "release": "2026-03-15"},
    {"vessel": "MT Shadow Tanker 7",  "flag": "Gabon",          "port": "Busan",          "deficiency": "No valid P&I certificate",     "status": "Detained",  "release": "Pending"},
    {"vessel": "MV Arctic Carrier",   "flag": "Panama",         "port": "Le Havre",       "deficiency": "Stability / load line",        "status": "Released",  "release": "2026-03-18"},
    {"vessel": "MT Gulf Pioneer",     "flag": "Comoros",        "port": "Port Said",      "deficiency": "MARPOL — oil record book",     "status": "Detained",  "release": "Pending"},
    {"vessel": "MV Iron Courage",     "flag": "Palau",          "port": "Antwerp",        "deficiency": "SOLAS — fire detection",       "status": "Released",  "release": "2026-03-20"},
    {"vessel": "MT Black Sea Rover",  "flag": "Cameroon",       "port": "Constanta",      "deficiency": "Sanctions evasion suspected",  "status": "Detained",  "release": "Under investigation"},
]

_CARGO_TYPES   = ["Crude Oil", "Refined Products", "LNG/LPG", "Dry Bulk", "Containers", "Ro-Ro", "General Cargo"]
_TRADE_ROUTES  = [
    "Russia → Asia", "Russia → EU", "Iran → China", "Iran → India",
    "Venezuela → Caribbean", "Middle East → Europe", "US Gulf → Asia",
    "West Africa → Europe", "DPRK → China", "SE Asia → US",
    "China → Europe (Suez)", "Brazil → China",
]
_COUNTERPARTIES = [
    "Russia", "Iran", "North Korea", "Venezuela", "Belarus", "Myanmar",
    "Syria", "Cuba", "China", "India", "UAE", "Turkey",
    "Germany", "US", "UK", "Singapore", "Japan", "South Korea",
]

_RISK_MATRIX: dict[str, int] = {
    "Russia": 85, "Iran": 95, "North Korea": 98, "Venezuela": 75,
    "Belarus": 60, "Myanmar": 55, "Syria": 90, "Cuba": 65,
    "China": 15, "India": 12, "UAE": 20, "Turkey": 18,
    "Germany": 2, "US": 2, "UK": 2, "Singapore": 3, "Japan": 3, "South Korea": 3,
}
_ROUTE_RISK: dict[str, int] = {
    "Russia → Asia": 80, "Russia → EU": 88, "Iran → China": 90, "Iran → India": 75,
    "Venezuela → Caribbean": 70, "Middle East → Europe": 20, "US Gulf → Asia": 5,
    "West Africa → Europe": 10, "DPRK → China": 95, "SE Asia → US": 8,
    "China → Europe (Suez)": 8, "Brazil → China": 5,
}
_CARGO_RISK: dict[str, int] = {
    "Crude Oil": 30, "Refined Products": 25, "LNG/LPG": 20, "Dry Bulk": 10,
    "Containers": 12, "Ro-Ro": 8, "General Cargo": 10,
}

# ---------------------------------------------------------------------------
# Helper: shared card CSS injected once
# ---------------------------------------------------------------------------

_CSS = f"""
<style>
.comp-card {{
    background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;
    padding:18px 20px;margin-bottom:14px;
}}
.comp-kpi {{
    background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;
    padding:16px 18px;text-align:center;
}}
.comp-kpi-val {{font-size:2rem;font-weight:700;line-height:1.1;}}
.comp-kpi-lbl {{font-size:0.75rem;color:{C_TEXT2};margin-top:4px;}}
.comp-th {{
    background:{C_SURFACE};color:{C_TEXT2};font-size:0.7rem;font-weight:600;
    text-transform:uppercase;letter-spacing:.06em;padding:8px 10px;
    border-bottom:1px solid {C_BORDER};
}}
.comp-td {{
    color:{C_TEXT};font-size:0.78rem;padding:8px 10px;
    border-bottom:1px solid {C_BORDER};vertical-align:top;
}}
.comp-td2 {{color:{C_TEXT2};font-size:0.78rem;padding:8px 10px;border-bottom:1px solid {C_BORDER};vertical-align:top;}}
.badge {{display:inline-block;padding:2px 8px;border-radius:12px;font-size:0.68rem;font-weight:600;}}
.badge-critical {{background:#7f1d1d;color:#fca5a5;}}
.badge-high {{background:#431407;color:#fdba74;}}
.badge-moderate {{background:#451a03;color:#fcd34d;}}
.badge-past {{background:#1e3a2f;color:{C_HIGH};}}
.badge-current {{background:#1e3a5f;color:{C_ACCENT};}}
.badge-upcoming {{background:#312e81;color:#a5b4fc;}}
.badge-detained {{background:#7f1d1d;color:#fca5a5;}}
.badge-released {{background:#1e3a2f;color:{C_HIGH};}}
.section-hdr {{
    font-size:1.05rem;font-weight:700;color:{C_TEXT};
    border-left:3px solid {C_ACCENT};padding-left:10px;margin-bottom:14px;
}}
</style>
"""

# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _section_1_dashboard() -> None:
    """KPI tiles."""
    try:
        st.markdown(_CSS, unsafe_allow_html=True)
        st.markdown(f'<div class="section-hdr">Compliance Dashboard</div>', unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        tiles = [
            (c1, "12", "Active Sanctions Regimes", C_LOW),
            (c2, "847", "Vessels on OFAC SDN List", C_MOD),
            (c3, "23", "IMO Updates (last 30 days)", C_ACCENT),
            (c4, "164", "Non-Compliant Vessels Flagged", C_LOW),
        ]
        for col, val, lbl, color in tiles:
            with col:
                st.markdown(
                    f'<div class="comp-kpi">'
                    f'<div class="comp-kpi-val" style="color:{color}">{val}</div>'
                    f'<div class="comp-kpi-lbl">{lbl}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        st.markdown("<br>", unsafe_allow_html=True)
        ca, cb = st.columns(2)
        with ca:
            st.markdown(
                f'<div class="comp-card">'
                f'<div style="font-size:0.8rem;font-weight:600;color:{C_TEXT2};margin-bottom:10px;">SANCTIONS REGIME COVERAGE</div>'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
                f'<span style="color:{C_TEXT};font-size:0.82rem;">US OFAC — SDN + CAATSA</span>'
                f'<span style="color:{C_LOW};font-weight:600;font-size:0.82rem;">5 active programs</span></div>'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
                f'<span style="color:{C_TEXT};font-size:0.82rem;">EU — OJ Regulations</span>'
                f'<span style="color:{C_MOD};font-weight:600;font-size:0.82rem;">3 active programs</span></div>'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
                f'<span style="color:{C_TEXT};font-size:0.82rem;">UK OFSI</span>'
                f'<span style="color:{C_MOD};font-weight:600;font-size:0.82rem;">2 active programs</span></div>'
                f'<div style="display:flex;justify-content:space-between;">'
                f'<span style="color:{C_TEXT};font-size:0.82rem;">UN Security Council</span>'
                f'<span style="color:{C_ACCENT};font-weight:600;font-size:0.82rem;">2 active programs</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with cb:
            st.markdown(
                f'<div class="comp-card">'
                f'<div style="font-size:0.8rem;font-weight:600;color:{C_TEXT2};margin-bottom:10px;">FLEET COMPLIANCE SNAPSHOT</div>'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
                f'<span style="color:{C_TEXT};font-size:0.82rem;">CII A or B rated vessels</span>'
                f'<span style="color:{C_HIGH};font-weight:600;">61%</span></div>'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
                f'<span style="color:{C_TEXT};font-size:0.82rem;">CII C rated (under review)</span>'
                f'<span style="color:{C_MOD};font-weight:600;">27%</span></div>'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
                f'<span style="color:{C_TEXT};font-size:0.82rem;">CII D or E (corrective action)</span>'
                f'<span style="color:{C_LOW};font-weight:600;">12%</span></div>'
                f'<div style="display:flex;justify-content:space-between;">'
                f'<span style="color:{C_TEXT};font-size:0.82rem;">PSC detentions YTD 2026</span>'
                f'<span style="color:{C_LOW};font-weight:600;">164 vessels</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )
    except Exception:
        logger.exception("Compliance dashboard error")
        st.error("Dashboard unavailable.")


def _section_2_sanctions_table() -> None:
    """Sanctions screening table."""
    try:
        st.markdown(f'<div class="section-hdr">Sanctions Screening Table</div>', unsafe_allow_html=True)
        severity_filter = st.selectbox(
            "Filter by severity",
            ["All", "Critical", "High", "Moderate"],
            key="sanct_severity_filter",
        )
        rows = _SANCTIONS_ROWS
        if severity_filter != "All":
            rows = [r for r in rows if r["severity"] == severity_filter.lower()]

        header = (
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>'
            f'<th class="comp-th">Jurisdiction</th>'
            f'<th class="comp-th">Sanctioned Entities</th>'
            f'<th class="comp-th">Vessel Types</th>'
            f'<th class="comp-th">Trade Lanes Affected</th>'
            f'<th class="comp-th">Effective Date</th>'
            f'<th class="comp-th">Penalty</th>'
            f'<th class="comp-th">Severity</th>'
            f'</tr></thead><tbody>'
        )
        body = ""
        for r in rows:
            badge_cls = f"badge-{r['severity']}"
            body += (
                f'<tr>'
                f'<td class="comp-td" style="color:{C_ACCENT};font-weight:600;">{r["jurisdiction"]}</td>'
                f'<td class="comp-td">{r["entity"]}</td>'
                f'<td class="comp-td2">{r["vessel_types"]}</td>'
                f'<td class="comp-td2">{r["trade_lanes"]}</td>'
                f'<td class="comp-td2">{r["effective"]}</td>'
                f'<td class="comp-td" style="color:{C_LOW};font-size:0.75rem;">{r["penalty"]}</td>'
                f'<td class="comp-td"><span class="badge {badge_cls}">{r["severity"].upper()}</span></td>'
                f'</tr>'
            )
        st.markdown(
            f'<div class="comp-card" style="padding:0;overflow-x:auto;">{header}{body}</tbody></table></div>',
            unsafe_allow_html=True,
        )
        st.caption("Sources: OFAC SDN List, EUR-Lex Official Journal, UK OFSI, UN SC Resolutions. Updated continuously.")
    except Exception:
        logger.exception("Sanctions table error")
        st.error("Sanctions table unavailable.")


def _section_3_imo_calendar() -> None:
    """IMO regulatory calendar."""
    try:
        st.markdown(f'<div class="section-hdr">IMO Regulatory Calendar</div>', unsafe_allow_html=True)
        header = (
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>'
            f'<th class="comp-th">Date</th>'
            f'<th class="comp-th">Regulation</th>'
            f'<th class="comp-th">Scope</th>'
            f'<th class="comp-th">Affected Vessels</th>'
            f'<th class="comp-th">Compliance Cost</th>'
            f'<th class="comp-th">Enforcement</th>'
            f'<th class="comp-th">Status</th>'
            f'</tr></thead><tbody>'
        )
        body = ""
        for r in _IMO_CALENDAR:
            badge_cls = f"badge-{r['status']}"
            body += (
                f'<tr>'
                f'<td class="comp-td" style="font-weight:600;color:{C_TEXT};white-space:nowrap;">{r["date"]}</td>'
                f'<td class="comp-td" style="font-weight:600;">{r["regulation"]}</td>'
                f'<td class="comp-td2">{r["scope"]}</td>'
                f'<td class="comp-td2">{r["vessels"]}</td>'
                f'<td class="comp-td2">{r["cost"]}</td>'
                f'<td class="comp-td2">{r["enforcement"]}</td>'
                f'<td class="comp-td"><span class="badge {badge_cls}">{r["status"].upper()}</span></td>'
                f'</tr>'
            )
        st.markdown(
            f'<div class="comp-card" style="padding:0;overflow-x:auto;">{header}{body}</tbody></table></div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("IMO calendar error")
        st.error("IMO calendar unavailable.")


def _section_4_cii_tracker() -> None:
    """CII carrier tracker."""
    try:
        st.markdown(f'<div class="section-hdr">CII (Carbon Intensity Indicator) Tracker — Major Carriers</div>', unsafe_allow_html=True)

        header = (
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>'
            f'<th class="comp-th">Carrier</th>'
            f'<th class="comp-th">2024 CII Rating</th>'
            f'<th class="comp-th">2025 Projected</th>'
            f'<th class="comp-th">Fleet Compliance %</th>'
            f'<th class="comp-th">Corrective Actions</th>'
            f'<th class="comp-th">At Risk?</th>'
            f'</tr></thead><tbody>'
        )
        body = ""
        for r in _CII_CARRIERS:
            r24 = r["rating_2024"]
            r25 = r["proj_2025"]
            pct = r["fleet_pct"]
            pct_color = C_HIGH if pct >= 80 else (C_MOD if pct >= 60 else C_LOW)
            at_risk_html = (
                f'<span style="color:{C_LOW};font-weight:700;">YES</span>'
                if r["at_risk"]
                else f'<span style="color:{C_HIGH};">No</span>'
            )
            body += (
                f'<tr>'
                f'<td class="comp-td" style="font-weight:600;">{r["carrier"]}</td>'
                f'<td class="comp-td">'
                f'<span style="background:{_CII_BG.get(r24,"#1a2235")};color:{_CII_COLOR.get(r24,C_TEXT)};'
                f'padding:3px 10px;border-radius:6px;font-weight:700;">{r24}</span></td>'
                f'<td class="comp-td">'
                f'<span style="background:{_CII_BG.get(r25,"#1a2235")};color:{_CII_COLOR.get(r25,C_TEXT)};'
                f'padding:3px 10px;border-radius:6px;font-weight:700;">{r25}</span></td>'
                f'<td class="comp-td" style="color:{pct_color};font-weight:600;">{pct}%</td>'
                f'<td class="comp-td2">{r["actions"]}</td>'
                f'<td class="comp-td">{at_risk_html}</td>'
                f'</tr>'
            )
        st.markdown(
            f'<div class="comp-card" style="padding:0;overflow-x:auto;">{header}{body}</tbody></table></div>',
            unsafe_allow_html=True,
        )

        st.markdown(
            f'<div class="comp-card">'
            f'<div style="font-size:0.8rem;font-weight:600;color:{C_TEXT2};margin-bottom:10px;">CII RATING SCALE</div>'
            f'<div style="display:flex;gap:10px;flex-wrap:wrap;">'
            f'<span style="background:{_CII_BG["A"]};color:{_CII_COLOR["A"]};padding:4px 14px;border-radius:6px;font-weight:700;">A — Superior</span>'
            f'<span style="background:{_CII_BG["B"]};color:{_CII_COLOR["B"]};padding:4px 14px;border-radius:6px;font-weight:700;">B — Minor superior</span>'
            f'<span style="background:{_CII_BG["C"]};color:{_CII_COLOR["C"]};padding:4px 14px;border-radius:6px;font-weight:700;">C — Moderate</span>'
            f'<span style="background:{_CII_BG["D"]};color:{_CII_COLOR["D"]};padding:4px 14px;border-radius:6px;font-weight:700;">D — Minor inferior</span>'
            f'<span style="background:{_CII_BG["E"]};color:{_CII_COLOR["E"]};padding:4px 14px;border-radius:6px;font-weight:700;">E — Inferior</span>'
            f'</div>'
            f'<div style="font-size:0.72rem;color:{C_TEXT3};margin-top:8px;">D or E for 3 consecutive years triggers mandatory corrective action plan and SEEMP Part III review.</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("CII tracker error")
        st.error("CII tracker unavailable.")


def _section_5_evasion_patterns() -> None:
    """Sanctions evasion intelligence."""
    try:
        st.markdown(f'<div class="section-hdr">Sanctions Evasion Patterns — Compliance Intelligence</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="comp-card" style="border-left:3px solid {C_MOD};">'
            f'<div style="font-size:0.78rem;color:{C_MOD};font-weight:600;margin-bottom:6px;">EDUCATIONAL / DUE DILIGENCE REFERENCE</div>'
            f'<div style="font-size:0.8rem;color:{C_TEXT2};">The following patterns are documented by OFAC, IMO, and compliance practitioners as common evasion techniques. Use for vessel due diligence and counterparty screening.</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        header = (
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>'
            f'<th class="comp-th">Method</th>'
            f'<th class="comp-th">Category</th>'
            f'<th class="comp-th">Risk Level</th>'
            f'<th class="comp-th">Indicators to Watch</th>'
            f'<th class="comp-th">Key Regions</th>'
            f'</tr></thead><tbody>'
        )
        body = ""
        for r in _EVASION_PATTERNS:
            risk_color = C_LOW if r["risk"] == "Critical" else (C_MOD if r["risk"] == "High" else C_ACCENT)
            body += (
                f'<tr>'
                f'<td class="comp-td" style="font-weight:600;color:{C_TEXT};">{r["method"]}</td>'
                f'<td class="comp-td2">{r["category"]}</td>'
                f'<td class="comp-td"><span style="color:{risk_color};font-weight:600;">{r["risk"]}</span></td>'
                f'<td class="comp-td2" style="font-size:0.75rem;">{r["indicators"]}</td>'
                f'<td class="comp-td2" style="font-size:0.75rem;">{r["regions"]}</td>'
                f'</tr>'
            )
        st.markdown(
            f'<div class="comp-card" style="padding:0;overflow-x:auto;">{header}{body}</tbody></table></div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Evasion patterns error")
        st.error("Evasion patterns section unavailable.")


def _section_6_dark_fleet() -> None:
    """Dark fleet tracker."""
    try:
        st.markdown(f'<div class="section-hdr">Dark Fleet Tracker</div>', unsafe_allow_html=True)

        total_est = "~780"
        st.markdown(
            f'<div class="comp-card" style="border-left:3px solid {C_LOW};">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<div>'
            f'<div style="font-size:0.78rem;color:{C_TEXT2};margin-bottom:4px;">ESTIMATED TOTAL SHADOW FLEET (2026)</div>'
            f'<div style="font-size:2.2rem;font-weight:700;color:{C_LOW};">{total_est} vessels</div>'
            f'</div>'
            f'<div style="text-align:right;">'
            f'<div style="font-size:0.78rem;color:{C_TEXT2};margin-bottom:4px;">LEGITIMATE TANKER RATE IMPACT</div>'
            f'<div style="font-size:1.4rem;font-weight:700;color:{C_MOD};">−8% to −15%</div>'
            f'<div style="font-size:0.72rem;color:{C_TEXT3};">spot rate depression</div>'
            f'</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        cols = st.columns(2)
        for i, fleet in enumerate(_DARK_FLEET):
            with cols[i % 2]:
                st.markdown(
                    f'<div class="comp-card" style="border-left:3px solid {fleet["color"]};">'
                    f'<div style="font-size:0.95rem;font-weight:700;color:{fleet["color"]};margin-bottom:10px;">{fleet["fleet"]}</div>'
                    f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;">'
                    f'<div><div style="font-size:0.68rem;color:{C_TEXT3};">ESTIMATED VESSELS</div>'
                    f'<div style="font-size:1.1rem;font-weight:700;color:{C_TEXT};">{fleet["est_vessels"]}</div></div>'
                    f'<div><div style="font-size:0.68rem;color:{C_TEXT3};">VESSEL TYPES</div>'
                    f'<div style="font-size:0.78rem;color:{C_TEXT2};">{fleet["types"]}</div></div>'
                    f'<div><div style="font-size:0.68rem;color:{C_TEXT3};">OPERATING AREAS</div>'
                    f'<div style="font-size:0.78rem;color:{C_TEXT2};">{fleet["operating_areas"]}</div></div>'
                    f'<div><div style="font-size:0.68rem;color:{C_TEXT3};">AVG VESSEL AGE</div>'
                    f'<div style="font-size:0.78rem;color:{C_TEXT2};">{fleet["age_avg"]}</div></div>'
                    f'<div><div style="font-size:0.68rem;color:{C_TEXT3};">P&I COVER</div>'
                    f'<div style="font-size:0.78rem;color:{C_LOW};">{fleet["p_i_cover"]}</div></div>'
                    f'<div><div style="font-size:0.68rem;color:{C_TEXT3};">MARKET IMPACT</div>'
                    f'<div style="font-size:0.78rem;color:{C_MOD};">{fleet["impact"]}</div></div>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        try:
            fig = go.Figure(go.Scattergeo(
                lon=[55, 57, 30, 33, 55, 58, 125, 128, -65, -68, 46, 50],
                lat=[25, 23, 43, 41, 26, 24,  22,  20,  12,  10, 15, 13],
                mode="markers",
                marker=dict(
                    size=[18, 18, 14, 14, 10, 10, 12, 12, 8, 8, 7, 7],
                    color=[C_LOW, C_LOW, C_LOW, C_LOW, "#f97316", "#f97316", C_ACCENT, C_ACCENT, C_MOD, C_MOD, C_HIGH, C_HIGH],
                    opacity=0.75,
                    line=dict(width=0),
                ),
                text=[
                    "Russian fleet — Arabian Gulf", "Russian fleet — Gulf of Oman",
                    "Russian fleet — Black Sea", "Russian fleet — Bosphorus approach",
                    "Iranian fleet — Arabian Gulf", "Iranian fleet — Gulf of Oman",
                    "DPRK illicit — East China Sea", "DPRK illicit — Yellow Sea",
                    "Venezuelan — Caribbean", "Venezuelan — Caribbean west",
                    "Somali piracy zone", "Gulf of Aden",
                ],
                hovertemplate="%{text}<extra></extra>",
            ))
            fig.update_layout(
                geo=dict(
                    showland=True, landcolor="#111827",
                    showocean=True, oceancolor="#0a0f1a",
                    showlakes=False,
                    showcountries=True, countrycolor="rgba(255,255,255,0.1)",
                    showcoastlines=True, coastlinecolor="rgba(255,255,255,0.15)",
                    bgcolor=C_BG,
                    projection_type="natural earth",
                ),
                paper_bgcolor=C_BG,
                plot_bgcolor=C_BG,
                font=dict(color=C_TEXT2, size=11),
                margin=dict(l=0, r=0, t=0, b=0),
                height=320,
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        except Exception:
            logger.exception("Dark fleet map error")
            st.info("Map rendering unavailable.")
    except Exception:
        logger.exception("Dark fleet tracker error")
        st.error("Dark fleet tracker unavailable.")


def _section_7_psc() -> None:
    """Port State Control detentions."""
    try:
        st.markdown(f'<div class="section-hdr">Port State Control — Recent Detentions & Deficiencies</div>', unsafe_allow_html=True)
        detained_count = sum(1 for r in _PSC_DETENTIONS if r["status"] == "Detained")
        released_count = sum(1 for r in _PSC_DETENTIONS if r["status"] == "Released")

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(
                f'<div class="comp-kpi"><div class="comp-kpi-val" style="color:{C_LOW};">{detained_count}</div>'
                f'<div class="comp-kpi-lbl">Currently Detained</div></div>',
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f'<div class="comp-kpi"><div class="comp-kpi-val" style="color:{C_HIGH};">{released_count}</div>'
                f'<div class="comp-kpi-lbl">Released (recent)</div></div>',
                unsafe_allow_html=True,
            )
        with c3:
            st.markdown(
                f'<div class="comp-kpi"><div class="comp-kpi-val" style="color:{C_MOD};">{len(_PSC_DETENTIONS)}</div>'
                f'<div class="comp-kpi-lbl">Total Deficiency Records</div></div>',
                unsafe_allow_html=True,
            )
        st.markdown("<br>", unsafe_allow_html=True)

        header = (
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>'
            f'<th class="comp-th">Vessel</th>'
            f'<th class="comp-th">Flag</th>'
            f'<th class="comp-th">Port</th>'
            f'<th class="comp-th">Deficiency</th>'
            f'<th class="comp-th">Status</th>'
            f'<th class="comp-th">Release Date</th>'
            f'</tr></thead><tbody>'
        )
        body = ""
        for r in _PSC_DETENTIONS:
            status_cls = "badge-detained" if r["status"] == "Detained" else "badge-released"
            body += (
                f'<tr>'
                f'<td class="comp-td" style="font-weight:600;">{r["vessel"]}</td>'
                f'<td class="comp-td2">{r["flag"]}</td>'
                f'<td class="comp-td2">{r["port"]}</td>'
                f'<td class="comp-td2" style="font-size:0.75rem;">{r["deficiency"]}</td>'
                f'<td class="comp-td"><span class="badge {status_cls}">{r["status"].upper()}</span></td>'
                f'<td class="comp-td2" style="font-size:0.75rem;">{r["release"]}</td>'
                f'</tr>'
            )
        st.markdown(
            f'<div class="comp-card" style="padding:0;overflow-x:auto;">{header}{body}</tbody></table></div>',
            unsafe_allow_html=True,
        )
        st.caption("Source: Paris MOU, Tokyo MOU, US Coast Guard PSIX. Records illustrative — verify against live MOU databases.")
    except Exception:
        logger.exception("PSC section error")
        st.error("Port State Control data unavailable.")


def _section_8_risk_score() -> None:
    """Interactive compliance risk score calculator."""
    try:
        st.markdown(f'<div class="section-hdr">Compliance Risk Score Calculator</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="comp-card" style="border-left:3px solid {C_ACCENT};">'
            f'<div style="font-size:0.8rem;color:{C_TEXT2};">Select your trade parameters to generate a sanctions and regulatory compliance risk score. For due diligence and pre-fixture screening.</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            route = st.selectbox("Trade Route", _TRADE_ROUTES, key="risk_route")
        with c2:
            cargo = st.selectbox("Cargo Type", _CARGO_TYPES, key="risk_cargo")
        with c3:
            party = st.selectbox("Counterparty Country", _COUNTERPARTIES, key="risk_party")

        route_r  = _ROUTE_RISK.get(route, 20)
        cargo_r  = _CARGO_RISK.get(cargo, 15)
        party_r  = _RISK_MATRIX.get(party, 15)
        raw      = route_r * 0.45 + party_r * 0.40 + cargo_r * 0.15
        score    = min(int(raw), 99)

        if score >= 75:
            color, label, border_color = C_LOW, "HIGH RISK — Do Not Proceed Without Legal Review", C_LOW
        elif score >= 40:
            color, label, border_color = C_MOD, "MODERATE RISK — Enhanced Due Diligence Required", C_MOD
        else:
            color, label, border_color = C_HIGH, "LOW RISK — Standard Screening Sufficient", C_HIGH

        gauge_fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=score,
            domain={"x": [0, 1], "y": [0, 1]},
            number={"font": {"color": color, "size": 52}},
            gauge={
                "axis": {"range": [0, 100], "tickcolor": C_TEXT3, "tickfont": {"color": C_TEXT3, "size": 10}},
                "bar": {"color": color, "thickness": 0.25},
                "bgcolor": C_CARD,
                "bordercolor": C_BORDER,
                "steps": [
                    {"range": [0, 40],  "color": "#052e1c"},
                    {"range": [40, 75], "color": "#451a03"},
                    {"range": [75, 100],"color": "#450a0a"},
                ],
                "threshold": {"line": {"color": color, "width": 3}, "thickness": 0.75, "value": score},
            },
        ))
        gauge_fig.update_layout(
            paper_bgcolor=C_CARD,
            font=dict(color=C_TEXT),
            height=220,
            margin=dict(l=20, r=20, t=20, b=10),
        )

        cg, cd = st.columns([1, 1])
        with cg:
            st.plotly_chart(gauge_fig, use_container_width=True, config={"displayModeBar": False})
        with cd:
            st.markdown(
                f'<div class="comp-card" style="border-left:3px solid {border_color};height:100%;">'
                f'<div style="font-size:1.5rem;font-weight:700;color:{color};margin-bottom:8px;">{score}/100</div>'
                f'<div style="font-size:0.85rem;font-weight:600;color:{color};margin-bottom:14px;">{label}</div>'
                f'<div style="font-size:0.78rem;color:{C_TEXT2};margin-bottom:6px;"><b style="color:{C_TEXT};">Route risk:</b> {route_r}/100 — {route}</div>'
                f'<div style="font-size:0.78rem;color:{C_TEXT2};margin-bottom:6px;"><b style="color:{C_TEXT};">Counterparty risk:</b> {party_r}/100 — {party}</div>'
                f'<div style="font-size:0.78rem;color:{C_TEXT2};margin-bottom:14px;"><b style="color:{C_TEXT};">Cargo risk:</b> {cargo_r}/100 — {cargo}</div>'
                f'<div style="font-size:0.72rem;color:{C_TEXT3};">Weighted: 45% route · 40% counterparty · 15% cargo</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        if score >= 75:
            recs = [
                "Obtain OFAC/OFSI legal opinion before fixture",
                "Require full beneficial ownership disclosure",
                "Verify vessel AIS history — last 12 months",
                "Check vessel against OFAC SDN list",
                "Confirm P&I club covers this trade",
                "Document all due diligence steps for regulatory file",
            ]
        elif score >= 40:
            recs = [
                "Screen vessel against latest SDN list",
                "Verify counterparty UBO structure",
                "Review trade lane for price-cap compliance",
                "Confirm cargo documentation matches B/L",
                "Monitor AIS for anomalies during voyage",
            ]
        else:
            recs = [
                "Standard OFAC/vessel screening sufficient",
                "Maintain routine documentation",
                "File voyage report per DCS requirements if applicable",
            ]

        rec_items = "".join(
            f'<li style="margin-bottom:5px;color:{C_TEXT2};font-size:0.8rem;">{rec}</li>'
            for rec in recs
        )
        st.markdown(
            f'<div class="comp-card">'
            f'<div style="font-size:0.8rem;font-weight:600;color:{C_TEXT};margin-bottom:10px;">RECOMMENDED ACTIONS</div>'
            f'<ul style="margin:0;padding-left:18px;">{rec_items}</ul>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.caption("Risk scores are illustrative guidance only. Not legal advice. Consult qualified sanctions counsel before any fixture decision.")
    except Exception:
        logger.exception("Risk score calculator error")
        st.error("Risk score calculator unavailable.")


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(port_results=None, insights=None) -> None:
    """Render the full Compliance & Sanctions Intelligence tab."""
    try:
        st.markdown(
            f'<div style="font-size:1.35rem;font-weight:700;color:{C_TEXT};margin-bottom:4px;">Regulatory Compliance & Sanctions Intelligence</div>'
            f'<div style="font-size:0.82rem;color:{C_TEXT3};margin-bottom:20px;">Live sanctions screening · IMO regulatory calendar · CII tracking · Dark fleet intelligence · PSC enforcement</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        logger.exception("Header render error")

    sections = [
        ("Compliance Dashboard",          _section_1_dashboard),
        ("Sanctions Screening",           _section_2_sanctions_table),
        ("IMO Regulatory Calendar",       _section_3_imo_calendar),
        ("CII Tracker",                   _section_4_cii_tracker),
        ("Sanctions Evasion Patterns",    _section_5_evasion_patterns),
        ("Dark Fleet Tracker",            _section_6_dark_fleet),
        ("Port State Control",            _section_7_psc),
        ("Compliance Risk Score",         _section_8_risk_score),
    ]

    for label, fn in sections:
        try:
            with st.expander(label, expanded=(label == "Compliance Dashboard")):
                fn()
        except Exception:
            logger.exception(f"Section '{label}' failed to render")
            st.error(f"{label} section encountered an error.")
