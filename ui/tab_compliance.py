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

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_BG,
    C_BORDER,
    C_CARD,
    C_CONV,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    alert_banner,
    apply_dark_layout,
    badge,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# ---------------------------------------------------------------------------
# Domain-specific palette (CII letter ratings — semantic, kept local)
# ---------------------------------------------------------------------------

_CII_COLOR = {"A": "#065f46", "B": "#2e9e6e", "C": "#c9962b", "D": "#c0392b", "E": "#7f1d1d"}
_CII_BG    = {"A": "#022c22", "B": "#052e1c", "C": "#451a03", "D": "#450a0a", "E": "#3b0808"}

# badge() needs hex colors — map semantic states to the WSJ palette constants.
_SEVERITY_COLOR = {"critical": C_LOW, "high": C_MOD, "moderate": C_MOD}
_STATUS_COLOR   = {"past": C_HIGH, "current": C_ACCENT, "upcoming": C_CONV}

# Provenance — illustrative compliance reference data
_COMPLIANCE_SOURCES = [
    {"name": "OFAC SDN List",      "kind": "modeled", "quality": "demo"},
    {"name": "EUR-Lex / UK OFSI",  "kind": "modeled", "quality": "demo"},
    {"name": "IMO MEPC circulars", "kind": "modeled", "quality": "demo"},
    {"name": "Paris MOU / Tokyo MOU PSC", "kind": "modeled", "quality": "demo"},
]

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
# Cell formatters for wsj_market_table()
# ---------------------------------------------------------------------------
# Mirrors the canonical pattern in ui/tab_results.py / ui/tab_rate_analytics.py.
# wsj_market_table renders cell strings as raw HTML inside <td>; these helpers
# only style content (font + conditional color); table CSS handles the rest.

def _mono(value: str, color: str = C_TEXT) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _cii_pill(rating: str) -> str:
    """Domain-specific CII letter-rating pill (A–E). Local because the
    semantic A→E color ramp is unique to MARPOL Annex VI."""
    fg = _CII_COLOR.get(rating, C_TEXT)
    bg = _CII_BG.get(rating, C_CARD)
    return (
        f'<span style="background:{bg};color:{fg};padding:3px 10px;'
        f'border-radius:6px;font-weight:700;font-family:var(--mono);">{rating}</span>'
    )


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _section_1_dashboard() -> None:
    try:
        metric_card_row(
            [
                {"label": "Active Sanctions Regimes",   "value": "12",  "accent": C_LOW},
                {"label": "Vessels on OFAC SDN List",   "value": "847", "accent": C_MOD},
                {"label": "IMO Updates (last 30 days)", "value": "23",  "accent": C_ACCENT},
                {"label": "Non-Compliant Vessels",      "value": "164", "accent": C_LOW},
            ],
            columns=4,
        )

        section_header(
            "Sanctions Regime Coverage & Fleet Compliance",
            "Snapshot of active programs and fleet CII distribution",
        )

        ca, cb = st.columns(2, gap="large")
        with ca:
            st.markdown(insight_card_html(
                title="US OFAC — SDN + CAATSA",
                score=0.95,
                action="Watch",
                rationale="5 active programs spanning Russia, Iran, North Korea, Venezuela, Myanmar.",
                category="SANCTIONS",
            ), unsafe_allow_html=True)
            st.markdown(insight_card_html(
                title="EU — Official Journal Regulations",
                score=0.80,
                action="Watch",
                rationale="3 active programs: Russia oil/LNG packages and Belarus transit goods.",
                category="SANCTIONS",
            ), unsafe_allow_html=True)
            st.markdown(insight_card_html(
                title="UK OFSI",
                score=0.70,
                action="Watch",
                rationale="2 active programs aligned with US/EU on Russia oil price cap and Iran.",
                category="SANCTIONS",
            ), unsafe_allow_html=True)
            st.markdown(insight_card_html(
                title="UN Security Council",
                score=0.65,
                action="Watch",
                rationale="2 active programs covering DPRK and Libya/Somalia regional measures.",
                category="SANCTIONS",
            ), unsafe_allow_html=True)
        with cb:
            st.markdown(insight_card_html(
                title="CII A or B rated vessels",
                score=0.61,
                action="Hold",
                rationale="61% of the global fleet rates A/B under MARPOL Annex VI Reg 28.",
                category="FLEET",
            ), unsafe_allow_html=True)
            st.markdown(insight_card_html(
                title="CII C rated (under review)",
                score=0.50,
                action="Watch",
                rationale="27% of the fleet sits at C — at risk of slippage to D without action.",
                category="FLEET",
            ), unsafe_allow_html=True)
            st.markdown(insight_card_html(
                title="CII D or E (corrective action)",
                score=0.20,
                action="Caution",
                rationale="12% of the fleet must file corrective action plans / SEEMP Part III review.",
                category="FLEET",
            ), unsafe_allow_html=True)
            st.markdown(insight_card_html(
                title="PSC detentions YTD 2026",
                score=0.30,
                action="Caution",
                rationale="164 vessels detained year-to-date across Paris MOU / Tokyo MOU regions.",
                category="ENFORCEMENT",
            ), unsafe_allow_html=True)

        st.markdown(source_footer(_COMPLIANCE_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("Compliance dashboard error")
        st.error("Dashboard unavailable.")


def _section_2_sanctions_table() -> None:
    try:
        severity_filter = st.selectbox(
            "Filter by severity",
            ["All", "Critical", "High", "Moderate"],
            key="sanct_severity_filter",
        )
        rows_data = _SANCTIONS_ROWS
        if severity_filter != "All":
            rows_data = [r for r in rows_data if r["severity"] == severity_filter.lower()]

        headers = [
            "Jurisdiction", "Sanctioned Entities", "Vessel Types",
            "Trade Lanes Affected", "Effective Date", "Penalty", "Severity",
        ]
        rows = [
            [
                _sans(r["jurisdiction"], color=C_ACCENT, weight=600),
                _sans(r["entity"], color=C_TEXT),
                _sans(r["vessel_types"]),
                _sans(r["trade_lanes"]),
                _sans(r["effective"]),
                _sans(r["penalty"], color=C_LOW),
                badge(r["severity"].upper(), _SEVERITY_COLOR.get(r["severity"], C_MOD)),
            ]
            for r in rows_data
        ]
        wsj_market_table(headers, rows)
        st.markdown(source_footer([
            {"name": "OFAC SDN List",       "kind": "modeled", "quality": "demo"},
            {"name": "EUR-Lex OJ",          "kind": "modeled", "quality": "demo"},
            {"name": "UK OFSI",             "kind": "modeled", "quality": "demo"},
            {"name": "UN SC Resolutions",   "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Sanctions table error")
        st.error("Sanctions table unavailable.")


def _section_3_imo_calendar() -> None:
    try:
        headers = [
            "Date", "Regulation", "Scope", "Affected Vessels",
            "Compliance Cost", "Enforcement", "Status",
        ]
        rows = [
            [
                _sans(r["date"], color=C_TEXT, weight=600),
                _sans(r["regulation"], color=C_TEXT, weight=600),
                _sans(r["scope"]),
                _sans(r["vessels"]),
                _sans(r["cost"]),
                _sans(r["enforcement"]),
                badge(r["status"].upper(), _STATUS_COLOR.get(r["status"], C_ACCENT)),
            ]
            for r in _IMO_CALENDAR
        ]
        wsj_market_table(headers, rows)
        st.markdown(source_footer([
            {"name": "IMO MEPC circulars",  "kind": "modeled", "quality": "demo"},
            {"name": "EU Reg 2023/957 / 2023/1805", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("IMO calendar error")
        st.error("IMO calendar unavailable.")


def _section_4_cii_tracker() -> None:
    try:
        headers = [
            "Carrier", "2024 CII", "2025 Proj",
            "Fleet Compliance %", "Corrective Actions", "At Risk?",
        ]
        rows = []
        for r in _CII_CARRIERS:
            pct = r["fleet_pct"]
            pct_color = C_HIGH if pct >= 80 else (C_MOD if pct >= 60 else C_LOW)
            at_risk = (
                _sans("YES", color=C_LOW, weight=700) if r["at_risk"]
                else _sans("No", color=C_HIGH)
            )
            rows.append([
                _sans(r["carrier"], color=C_TEXT, weight=600),
                _cii_pill(r["rating_2024"]),
                _cii_pill(r["proj_2025"]),
                _mono(f"{pct}%", color=pct_color),
                _sans(r["actions"]),
                at_risk,
            ])
        wsj_market_table(headers, rows)

        section_header(
            "CII Rating Scale",
            "D or E for 3 consecutive years triggers mandatory corrective action plan and SEEMP Part III review.",
        )

        scale_headers = ["Rating", "Description"]
        scale_rows = [
            [_cii_pill(k), _sans(desc, color=C_TEXT)]
            for k, desc in [
                ("A", "Superior"),
                ("B", "Minor superior"),
                ("C", "Moderate"),
                ("D", "Minor inferior"),
                ("E", "Inferior"),
            ]
        ]
        wsj_market_table(scale_headers, scale_rows)

        st.markdown(source_footer([
            {"name": "MARPOL Annex VI Reg 28 (CII)", "kind": "modeled", "quality": "demo"},
            {"name": "Carrier sustainability disclosures", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("CII tracker error")
        st.error("CII tracker unavailable.")


def _section_5_evasion_patterns() -> None:
    try:
        st.markdown(insight_card_html(
            title="Educational / Due Diligence Reference",
            score=0.50,
            action="Watch",
            rationale=(
                "The following patterns are documented by OFAC, IMO, and compliance "
                "practitioners as common evasion techniques. Use for vessel due diligence "
                "and counterparty screening."
            ),
            category="REFERENCE",
        ), unsafe_allow_html=True)

        headers = ["Method", "Category", "Risk Level", "Indicators to Watch", "Key Regions"]
        rows = []
        for r in _EVASION_PATTERNS:
            risk_color = C_LOW if r["risk"] == "Critical" else (C_MOD if r["risk"] == "High" else C_ACCENT)
            rows.append([
                _sans(r["method"], color=C_TEXT, weight=600),
                _sans(r["category"]),
                _sans(r["risk"], color=risk_color, weight=600),
                _sans(r["indicators"]),
                _sans(r["regions"]),
            ])
        wsj_market_table(headers, rows)
        st.markdown(source_footer([
            {"name": "OFAC compliance guidance",     "kind": "modeled", "quality": "demo"},
            {"name": "IMO MSC.1/Circ.1598 (AIS)",    "kind": "modeled", "quality": "demo"},
            {"name": "Industry due-diligence playbooks", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Evasion patterns error")
        st.error("Evasion patterns section unavailable.")


def _section_6_dark_fleet() -> None:
    try:
        metric_card_row(
            [
                {"label": "Estimated Total Shadow Fleet (2026)",
                 "value": "~780 vessels", "accent": C_LOW,
                 "sublabel": "Russia + Iran + Venezuela + DPRK"},
                {"label": "Legitimate Tanker Rate Impact",
                 "value": "−8% to −15%", "accent": C_MOD,
                 "sublabel": "spot rate depression"},
            ],
            columns=2,
        )

        section_header(
            "Fleet Breakdown by Sponsor",
            "Estimated vessel counts, operating areas, and market impact",
        )

        fleet_headers = [
            "Fleet", "Est Vessels", "Vessel Types", "Operating Areas",
            "Avg Age", "P&I Cover", "Market Impact",
        ]
        fleet_rows = []
        for fleet in _DARK_FLEET:
            fleet_rows.append([
                _sans(fleet["fleet"], color=fleet["color"], weight=700),
                _mono(fleet["est_vessels"], color=C_TEXT),
                _sans(fleet["types"]),
                _sans(fleet["operating_areas"]),
                _sans(fleet["age_avg"]),
                _sans(fleet["p_i_cover"], color=C_LOW),
                _sans(fleet["impact"], color=C_MOD),
            ])
        wsj_market_table(fleet_headers, fleet_rows)

        section_header(
            "Operating Area Heatmap",
            "Approximate concentrations of dark-fleet activity",
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
            apply_dark_layout(fig, height=320, showlegend=False)
            fig.update_layout(
                margin=dict(l=0, r=0, t=0, b=0),
                geo=dict(
                    showland=True, landcolor=C_CARD,
                    showocean=True, oceancolor=C_BG,
                    showlakes=False,
                    showcountries=True, countrycolor="rgba(255,255,255,0.1)",
                    showcoastlines=True, coastlinecolor="rgba(255,255,255,0.15)",
                    bgcolor=C_BG,
                    projection_type="natural earth",
                ),
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        except Exception:
            logger.exception("Dark fleet map error")
            st.info("Map rendering unavailable.")

        st.markdown(source_footer([
            {"name": "S&P Global / Lloyd's List dark-fleet estimates", "kind": "modeled", "quality": "demo"},
            {"name": "Open-source AIS reconciliation",                 "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Dark fleet tracker error")
        st.error("Dark fleet tracker unavailable.")


def _section_7_psc() -> None:
    try:
        detained_count = sum(1 for r in _PSC_DETENTIONS if r["status"] == "Detained")
        released_count = sum(1 for r in _PSC_DETENTIONS if r["status"] == "Released")

        metric_card_row(
            [
                {"label": "Currently Detained",        "value": str(detained_count),      "accent": C_LOW},
                {"label": "Released (recent)",         "value": str(released_count),      "accent": C_HIGH},
                {"label": "Total Deficiency Records",  "value": str(len(_PSC_DETENTIONS)), "accent": C_MOD},
            ],
            columns=3,
        )

        headers = ["Vessel", "Flag", "Port", "Deficiency", "Status", "Release Date"]
        rows = [
            [
                _sans(r["vessel"], color=C_TEXT, weight=600),
                _sans(r["flag"]),
                _sans(r["port"]),
                _sans(r["deficiency"]),
                badge(r["status"].upper(), C_LOW if r["status"] == "Detained" else C_HIGH),
                _sans(r["release"]),
            ]
            for r in _PSC_DETENTIONS
        ]
        wsj_market_table(headers, rows)
        st.markdown(source_footer([
            {"name": "Paris MOU",         "kind": "modeled", "quality": "demo"},
            {"name": "Tokyo MOU",         "kind": "modeled", "quality": "demo"},
            {"name": "US Coast Guard PSIX", "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("PSC section error")
        st.error("Port State Control data unavailable.")


def _section_8_risk_score() -> None:
    try:
        st.markdown(insight_card_html(
            title="Compliance Risk Calculator",
            score=0.50,
            action="Watch",
            rationale=(
                "Select your trade parameters to generate a sanctions and regulatory "
                "compliance risk score. For due diligence and pre-fixture screening."
            ),
            category="CALCULATOR",
        ), unsafe_allow_html=True)

        c1, c2, c3 = st.columns(3, gap="large")
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
            color, label, action = C_LOW,  "HIGH RISK — Do Not Proceed Without Legal Review", "Caution"
            score_norm = 0.85
        elif score >= 40:
            color, label, action = C_MOD,  "MODERATE RISK — Enhanced Due Diligence Required", "Watch"
            score_norm = 0.55
        else:
            color, label, action = C_HIGH, "LOW RISK — Standard Screening Sufficient", "Hold"
            score_norm = 0.20

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
        apply_dark_layout(gauge_fig, height=220)
        gauge_fig.update_layout(margin=dict(l=20, r=20, t=20, b=10))

        cg, cd = st.columns([1, 1], gap="large")
        with cg:
            st.plotly_chart(gauge_fig, use_container_width=True, config={"displayModeBar": False})
        with cd:
            st.markdown(insight_card_html(
                title=f"{score}/100 — {label}",
                score=score_norm,
                action=action,
                rationale=(
                    f"Route risk {route_r}/100 ({route}) · "
                    f"Counterparty risk {party_r}/100 ({party}) · "
                    f"Cargo risk {cargo_r}/100 ({cargo}). "
                    f"Weighted: 45% route · 40% counterparty · 15% cargo."
                ),
                category="RISK",
            ), unsafe_allow_html=True)

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

        section_header(
            "Recommended Actions",
            "Step-by-step due-diligence checklist scaled to the calculated risk band",
        )

        rec_headers = ["#", "Action"]
        rec_rows = [
            [_mono(f"{i+1:02d}", color=color), _sans(rec, color=C_TEXT)]
            for i, rec in enumerate(recs)
        ]
        wsj_market_table(rec_headers, rec_rows)

        st.caption("Risk scores are illustrative guidance only. Not legal advice. Consult qualified sanctions counsel before any fixture decision.")
        st.markdown(source_footer([
            {"name": "Internal compliance heuristic", "kind": "modeled", "quality": "demo"},
            {"name": "OFAC / OFSI public guidance",   "kind": "modeled", "quality": "demo"},
        ]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Risk score calculator error")
        st.error("Risk score calculator unavailable.")


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(port_results=None, insights=None, *args, **kwargs) -> None:
    """Render the full Compliance & Sanctions Intelligence tab."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('compliance'):
        try:
            page_header(
                title="Regulatory Compliance & Sanctions Intelligence",
                subtitle="Illustrative sanctions / IMO calendar / CII / dark-fleet / PSC reference dashboard — sample data, not a live screen",
                badge_text="COMPLIANCE",
                badge_color=C_ACCENT,
            )
        except Exception:
            logger.exception("Header render error")

        try:
            alert_banner(
                "<b>ILLUSTRATIVE / SAMPLE</b> — this dashboard renders curated reference and "
                "modeled example data (vessel counts, SDN/detention tables, CII ratings), not a "
                "live sanctions or port-state-control screen. Do not use for real compliance "
                "decisions. A live screening engine is a separate, future capability.",
                level="warning",
            )
        except Exception:
            logger.exception("Compliance illustrative banner render error")

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

        for idx, (label, fn) in enumerate(sections):
            if idx > 0:
                section_divider()
            try:
                with st.expander(label, expanded=(label == "Compliance Dashboard")):
                    fn()
            except Exception:
                logger.exception(f"Section '{label}' failed to render")
                st.error(f"{label} section encountered an error.")
