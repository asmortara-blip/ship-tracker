"""
trade_finance.py
================
Trade finance indicators and risk scoring for container shipping demand analysis.

Letters of credit, documentary collections, and open-account financing are the
lubricant of global trade and function as a leading indicator for shipping demand.
Tighter credit conditions → higher import financing costs → reduced order flow
→ lower container demand → lower freight rates.

Key functions
-------------
build_trade_finance_indicators()
    Hydrate TRADE_FINANCE_INDICATORS with static 2025/2026 values and signals.

compute_regional_finance_risk() -> list[TradeFinanceRiskScore]
    Score eight trade regions by credit availability and default risk.

compute_interest_rate_impact_on_shipping(rate_pct) -> dict
    Model the demand-suppression effect of the current rate environment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from loguru import logger


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TradeFinanceIndicator:
    """One trade-finance indicator with current reading and shipping signal."""

    indicator_name: str           # Human-readable name
    current_value: float          # Most-recent observation (units vary by indicator)
    yoy_change_pct: float         # Year-over-year percentage change (+/-)
    signal: str                   # "BULLISH" | "BEARISH" | "NEUTRAL"
    shipping_lead_weeks: int      # Weeks this indicator leads shipping demand
    description: str              # One-sentence explanation of the metric
    data_source: str              # Primary data provider / methodology


@dataclass
class TradeFinanceRiskScore:
    """Credit-availability risk score for a single trade region."""

    region: str                   # Geographic trade region name
    score: float                  # 0.0 (no risk) to 1.0 (maximum risk)
    primary_risk: str             # Dominant risk factor description
    affected_routes: List[str]    # Route IDs materially affected
    rate_impact_pct: float        # Estimated freight-rate uplift from this risk (%)


# ---------------------------------------------------------------------------
# Static indicator catalogue — 2025/2026 values
# ---------------------------------------------------------------------------

# fmt: off
TRADE_FINANCE_INDICATORS: Dict[str, dict] = {
    # ── 1. ICC Global Trade Finance Index ─────────────────────────────────
    "ICC_GTFI": {
        "indicator_name": "ICC Global Trade Finance Index",
        "current_value": 62.0,        # composite score /100
        "yoy_change_pct": -5.0,       # declining from 2024's 65
        "signal": "BEARISH",          # below 2021 peak of 78 → tighter conditions
        "shipping_lead_weeks": 8,
        "description": (
            "Composite measure of trade finance availability across 95 banks;"
            " a score below 65/100 signals restricted credit access, suppressing"
            " import order volumes and container demand 6-10 weeks ahead."
        ),
        "data_source": "ICC Banking Commission Annual Survey 2025",
    },

    # ── 2. Letter of Credit Volume ─────────────────────────────────────────
    "LC_VOLUME": {
        "indicator_name": "Letter of Credit Volume (Global)",
        "current_value": -8.0,        # YoY change (%) — used as primary value
        "yoy_change_pct": -8.0,
        "signal": "BEARISH",          # declining LC signals riskier trade environment
        "shipping_lead_weeks": 6,
        "description": (
            "Global L/C issuance (BIS proxy) declining 8% YoY as open-account"
            " financing gains share; falling L/C volumes with existing counterparties"
            " can paradoxically precede demand dips with new counterparties."
        ),
        "data_source": "BIS Payment Statistics / SWIFT Trade Services",
    },

    # ── 3. Trade Credit Insurance Premiums ────────────────────────────────
    "TCI_PREMIUM": {
        "indicator_name": "Trade Credit Insurance Premiums (China)",
        "current_value": 15.0,        # YoY premium increase for China counterparties (%)
        "yoy_change_pct": 15.0,
        "signal": "BEARISH",          # elevated premiums = bank perception of higher risk
        "shipping_lead_weeks": 10,
        "description": (
            "Insurance premiums for China-counterparty trade credit are elevated"
            " +15% YoY, signalling that banks perceive higher default risk for"
            " Asia-origin trade transactions, tightening available credit lines."
        ),
        "data_source": "Euler Hermes / Coface Global Risk Assessment 2025",
    },

    # ── 4. SOFR (USD Cost of Trade Finance Borrowing) ─────────────────────
    "SOFR_RATE": {
        "indicator_name": "USD SOFR (Cost of Trade Finance Borrowing)",
        "current_value": 5.2,         # annualised % (elevated vs 0.05% in 2021)
        "yoy_change_pct": -0.5,       # modest easing from 5.7% peak
        "signal": "BEARISH",          # 5.2% still heavily elevated vs neutral ~2%
        "shipping_lead_weeks": 12,
        "description": (
            "The Secured Overnight Financing Rate at 5.2% makes USD-denominated"
            " trade finance significantly more expensive than the 2021 trough (0.05%),"
            " raising importer financing costs and discouraging inventory build."
        ),
        "data_source": "Federal Reserve Bank of New York / FRED (SOFR)",
    },

    # ── 5. FX Hedging Cost (USD/CNY 3-month forward) ──────────────────────
    "FX_HEDGE_USDCNY": {
        "indicator_name": "FX Hedging Cost — USD/CNY 3-Month Forward",
        "current_value": 2.8,         # annualised hedging cost (%)
        "yoy_change_pct": 8.0,        # risen as USD has strengthened
        "signal": "BEARISH",          # higher cost = dearer for Chinese importers
        "shipping_lead_weeks": 8,
        "description": (
            "The annualised cost of hedging USD/CNY exposure via 3-month forward"
            " contracts has risen to 2.8% as USD strength persists, making"
            " Chinese importers pay more to lock in exchange rates, dampening"
            " trans-Pacific import demand."
        ),
        "data_source": "Bloomberg FX Forward Rates / People's Bank of China",
    },

    # ── 6. Supply Chain Finance Programs ──────────────────────────────────
    "SCF_VOLUME": {
        "indicator_name": "Supply Chain Finance — Approved Receivables Volume",
        "current_value": 12.0,        # YoY growth (%)
        "yoy_change_pct": 12.0,
        "signal": "BULLISH",          # growing SCF = more trade liquidity
        "shipping_lead_weeks": 4,
        "description": (
            "Volume of approved receivables in reverse-factoring / supply-chain"
            " finance programmes growing 12% YoY, improving working-capital"
            " access for exporters and supporting trade flows."
        ),
        "data_source": "BCR Publishing Global SCF Report 2025",
    },

    # ── 7. Bank Willingness to Lend (Trade Credit Survey) ─────────────────
    "BANK_LENDING": {
        "indicator_name": "Bank Willingness to Lend — Trade Credit (Net %)",
        "current_value": -8.0,        # net % tightening (negative = net tightening)
        "yoy_change_pct": -3.0,       # slightly more tightening YoY
        "signal": "BEARISH",          # net tightening constrains import financing
        "shipping_lead_weeks": 10,
        "description": (
            "Senior loan officer surveys show a net 8% of banks are tightening"
            " trade-credit standards — primarily for emerging-market counterparties —"
            " reducing the pool of bankable trade transactions globally."
        ),
        "data_source": "ICC Global Survey on Trade Finance / Federal Reserve SLOOS",
    },

    # ── 8. Emerging Market Default Risk (EM CDS Spreads) ──────────────────
    "EM_CDS_SPREAD": {
        "indicator_name": "Emerging Market Default Risk (EM Sovereign CDS)",
        "current_value": 285.0,       # basis points (5Y EM aggregate CDS spread)
        "yoy_change_pct": 12.0,       # widened from ~255bp in 2024
        "signal": "BEARISH",          # wider EM CDS = more expensive import finance
        "shipping_lead_weeks": 12,
        "description": (
            "The 5-year EM sovereign CDS aggregate at 285bp signals elevated"
            " counterparty risk, raising the cost of trade finance letters of"
            " credit and open-account financing for EM importers, dampening"
            " demand on Asia-Africa and Latin America routes."
        ),
        "data_source": "JPMorgan EMBI / Markit EM Sovereign CDS Index",
    },

    # ── 9. ADB Trade Finance Gap ──────────────────────────────────────────
    "ADB_FINANCE_GAP": {
        "indicator_name": "ADB Global Trade Finance Gap",
        "current_value": 2500.0,      # USD billion unmet trade finance demand
        "yoy_change_pct": 2.0,        # gap widening slightly
        "signal": "BEARISH",          # unmet demand = unrealised shipping volumes
        "shipping_lead_weeks": 16,
        "description": (
            "The Asian Development Bank estimates $2.5 trillion in unmet global"
            " trade finance demand as of 2024-2025, disproportionately affecting"
            " SME exporters in Asia and Africa, representing suppressed but"
            " latent shipping demand that could be unlocked by credit expansion."
        ),
        "data_source": "Asian Development Bank Trade Finance Gaps Survey 2024",
    },

    # ── 10. SWIFT Trade Message Volumes ──────────────────────────────────
    "SWIFT_TRADE_MSG": {
        "indicator_name": "SWIFT Trade Finance Message Volumes",
        "current_value": 5.0,         # YoY growth (%) in MT 700/710/720 messages
        "yoy_change_pct": 5.0,
        "signal": "BULLISH",          # rising SWIFT volumes = actual trade activity
        "shipping_lead_weeks": 3,
        "description": (
            "SWIFT MT 700-series (documentary credit) message volumes rising 5%"
            " YoY — the most granular leading indicator of actual trade settlement"
            " activity, leading container booking rates by approximately 3 weeks."
        ),
        "data_source": "SWIFT Watch Trade Finance Activity Report 2025",
    },
}
# fmt: on


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _classify_signal(yoy_change_pct: float, inverse: bool = False, threshold: float = 2.0) -> str:
    """Map a YoY change to BULLISH / BEARISH / NEUTRAL, respecting inverse series."""
    if abs(yoy_change_pct) < threshold:
        return "NEUTRAL"
    raw_up = yoy_change_pct > 0
    is_bullish = (raw_up and not inverse) or (not raw_up and inverse)
    return "BULLISH" if is_bullish else "BEARISH"


# ---------------------------------------------------------------------------
# Public API — indicators
# ---------------------------------------------------------------------------

def build_trade_finance_indicators() -> List[TradeFinanceIndicator]:
    """
    Instantiate all TRADE_FINANCE_INDICATORS as typed dataclass objects.

    Returns
    -------
    List of :class:`TradeFinanceIndicator`, one per catalogue entry.
    """
    results: List[TradeFinanceIndicator] = []
    for key, meta in TRADE_FINANCE_INDICATORS.items():
        ind = TradeFinanceIndicator(
            indicator_name=meta["indicator_name"],
            current_value=meta["current_value"],
            yoy_change_pct=meta["yoy_change_pct"],
            signal=meta["signal"],
            shipping_lead_weeks=meta["shipping_lead_weeks"],
            description=meta["description"],
            data_source=meta["data_source"],
        )
        results.append(ind)
        logger.debug(
            "TradeFinanceIndicator {name}: value={val} yoy={yoy:+.1f}% signal={sig}",
            name=key,
            val=meta["current_value"],
            yoy=meta["yoy_change_pct"],
            sig=meta["signal"],
        )
    logger.info(
        "build_trade_finance_indicators: {n} indicators loaded", n=len(results)
    )
    return results


def compute_trade_finance_composite(indicators: List[TradeFinanceIndicator] | None = None) -> dict:
    """
    Compute a composite trade-finance health score from the indicator list.

    Returns
    -------
    dict with:
        composite_score     float 0-1  (higher = healthier credit conditions)
        bullish_count       int
        bearish_count       int
        neutral_count       int
        dominant_signal     str  "BULLISH" | "BEARISH" | "NEUTRAL"
    """
    if indicators is None:
        indicators = build_trade_finance_indicators()

    bullish = sum(1 for i in indicators if i.signal == "BULLISH")
    bearish = sum(1 for i in indicators if i.signal == "BEARISH")
    neutral = sum(1 for i in indicators if i.signal == "NEUTRAL")
    total = len(indicators)

    if total == 0:
        return {
            "composite_score": 0.5,
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "dominant_signal": "NEUTRAL",
        }

    # Simple equal-weighted signal score: BULLISH=+1, NEUTRAL=0, BEARISH=-1
    raw_score = (bullish - bearish) / total     # range [-1, +1]
    composite_score = round((raw_score + 1.0) / 2.0, 4)  # normalise to [0, 1]

    if bullish > bearish:
        dominant = "BULLISH"
    elif bearish > bullish:
        dominant = "BEARISH"
    else:
        dominant = "NEUTRAL"

    logger.info(
        "Trade finance composite: score={s:.3f} bull={b} bear={be} neutral={n}",
        s=composite_score, b=bullish, be=bearish, n=neutral,
    )
    return {
        "composite_score": composite_score,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": neutral,
        "dominant_signal": dominant,
    }


# ---------------------------------------------------------------------------
# Public API — regional risk scoring
# ---------------------------------------------------------------------------

# Regional risk parameters (static 2025/2026)
# Each tuple: (region, base_score, primary_risk, affected_routes, rate_impact_pct)
_REGIONAL_RISK_DATA: List[tuple] = [
    (
        "Russia / CIS",
        0.95,
        "SWIFT exclusion, western financial sanctions, USD payment restrictions",
        ["EUROPE_RUSSIA", "BSEA_TRANSIT", "ARCTIC_ROUTE"],
        5.0,
    ),
    (
        "Iran",
        0.90,
        "US/EU secondary sanctions on banking, OFAC blocking of USD transactions",
        ["HORMUZ_TRANSIT", "MIDEAST_GULF", "INDIA_WEST"],
        3.5,
    ),
    (
        "Argentina",
        0.78,
        "Capital controls, peso convertibility restrictions, BCRA FX rationing",
        ["SAEC_SOUTHBOUND", "LATAM_EXPORT"],
        4.2,
    ),
    (
        "Venezuela",
        0.82,
        "US OFAC sanctions, banking correspondent cutoff, USD access blocked",
        ["CARIB_WEST", "LATAM_NORTH"],
        2.8,
    ),
    (
        "Sub-Saharan Africa (ex South Africa)",
        0.62,
        "Trade finance gap, limited LC correspondent banking, currency volatility",
        ["AFRICA_WEST", "AFRICA_EAST", "CAPE_GOOD_HOPE"],
        2.1,
    ),
    (
        "China (counterparty risk elevated)",
        0.38,
        "Elevated TCI premiums, geopolitical decoupling risk, USD/CNY hedging cost",
        ["TRANSPACIFIC_EB", "TRANSPACIFIC_WB", "ASIA_EUROPE"],
        1.5,
    ),
    (
        "North America / Western Europe",
        0.12,
        "Elevated SOFR rates increasing borrowing cost; otherwise robust credit",
        ["TRANSATLANTIC", "TRANSPACIFIC_EB", "GULF_COAST"],
        0.5,
    ),
    (
        "Southeast Asia / ASEAN",
        0.28,
        "Some currency volatility; generally adequate trade finance infrastructure",
        ["INTRAASIA", "ASIA_EUROPE", "AUSTRALIA_ASIA"],
        0.8,
    ),
]


def compute_regional_finance_risk() -> List[TradeFinanceRiskScore]:
    """
    Score trade regions by credit tightness and sanctions exposure.

    Higher risk score (closer to 1.0) indicates tighter credit conditions,
    which translates to lower import financing capacity and reduced shipping
    demand on affected routes.

    Returns
    -------
    List of :class:`TradeFinanceRiskScore` sorted by risk score descending.
    """
    results: List[TradeFinanceRiskScore] = []
    for region, score, primary_risk, routes, rate_impact in _REGIONAL_RISK_DATA:
        entry = TradeFinanceRiskScore(
            region=region,
            score=round(score, 4),
            primary_risk=primary_risk,
            affected_routes=routes,
            rate_impact_pct=round(rate_impact, 2),
        )
        results.append(entry)
        logger.debug(
            "Regional finance risk [{region}]: score={s:.2f} rate_impact={r:+.1f}%",
            region=region, s=score, r=rate_impact,
        )

    results.sort(key=lambda x: x.score, reverse=True)
    logger.info(
        "compute_regional_finance_risk: {n} regions scored, highest risk = {top}",
        n=len(results), top=results[0].region if results else "n/a",
    )
    return results


# ---------------------------------------------------------------------------
# Public API — interest rate impact model
# ---------------------------------------------------------------------------

# Rate hiking cycle reference: Fed began hiking March 2022 from ~0.08%
_RATE_CYCLE_START_RATE: float = 0.08        # effective Fed funds rate March 2022 (%)
_RATE_CYCLE_START_LABEL: str = "March 2022"

# Elasticity: each 1 percentage-point increase in rates reduces container
# demand by approximately 2% over a 6-12 month lag window.
_DEMAND_ELASTICITY_PCT_PER_RATE_PT: float = 2.0   # % demand reduction per 1pp rate rise


def compute_interest_rate_impact_on_shipping(rate_pct: float) -> dict:
    """
    Model demand-suppression effects of the current interest rate environment.

    Higher interest rates increase inventory carrying costs for importers,
    incentivising destocking (or preventing restocking), which reduces
    container booking volumes with a 6-12 month lag.

    Mechanism
    ---------
    Each 1 percentage-point increase in USD benchmark rate above neutral
    (~2%) reduces container demand by approximately 2%, with a 6-12 month
    transmission lag.

    Parameters
    ----------
    rate_pct:
        Current benchmark interest rate as a percentage (e.g. 5.25).

    Returns
    -------
    dict with:
        current_rate_pct        float
        neutral_rate_pct        float    (assumed long-run neutral = 2.5%)
        excess_rate_pp          float    (current - neutral, clipped at 0)
        estimated_demand_impact_pct  float   (negative = suppression)
        cumulative_impact_since_2022_pct  float
        transmission_lag_weeks  dict     {"low": 24, "high": 52}
        affected_routes         list[str]
        scenario_label          str
        narrative               str
    """
    _NEUTRAL_RATE: float = 2.5   # long-run neutral Fed funds rate (%)

    excess_pp = max(0.0, rate_pct - _NEUTRAL_RATE)
    # Demand impact from current excess over neutral
    demand_impact_pct = round(-excess_pp * _DEMAND_ELASTICITY_PCT_PER_RATE_PT, 2)

    # Cumulative impact since hiking cycle began (March 2022 from ~0.08%)
    cumulative_excess_pp = max(0.0, rate_pct - _RATE_CYCLE_START_RATE)
    cumulative_impact_pct = round(
        -cumulative_excess_pp * _DEMAND_ELASTICITY_PCT_PER_RATE_PT, 2
    )

    # Qualitative scenario label
    if rate_pct >= 5.0:
        scenario = "HIGHLY RESTRICTIVE — significant demand headwind"
    elif rate_pct >= 3.5:
        scenario = "RESTRICTIVE — moderate demand suppression"
    elif rate_pct >= 2.5:
        scenario = "NEAR NEUTRAL — limited direct demand impact"
    else:
        scenario = "ACCOMMODATIVE — supportive of inventory build and shipping demand"

    affected_routes = [
        "TRANSPACIFIC_EB",
        "TRANSPACIFIC_WB",
        "ASIA_EUROPE",
        "TRANSATLANTIC",
        "LATAM_EAST_COAST",
    ]

    narrative = (
        "At "
        + str(round(rate_pct, 2))
        + "% ("
        + str(round(excess_pp, 2))
        + "pp above long-run neutral of "
        + str(_NEUTRAL_RATE)
        + "%), the current rate environment is estimated to suppress global"
        " container demand by approximately "
        + str(abs(demand_impact_pct))
        + "% relative to a neutral-rate baseline."
        " Cumulatively, rate hikes since "
        + _RATE_CYCLE_START_LABEL
        + " (from "
        + str(_RATE_CYCLE_START_RATE)
        + "%) have suppressed container demand by an estimated "
        + str(abs(cumulative_impact_pct))
        + "% — materialising primarily through destocking cycles,"
        " reduced import order frequencies, and higher inventory carrying costs."
    )

    logger.info(
        "Rate impact model: rate={r:.2f}% excess={e:.2f}pp"
        " demand_impact={d:.1f}% cumulative={c:.1f}%",
        r=rate_pct, e=excess_pp, d=demand_impact_pct, c=cumulative_impact_pct,
    )

    return {
        "current_rate_pct": round(rate_pct, 3),
        "neutral_rate_pct": _NEUTRAL_RATE,
        "excess_rate_pp": round(excess_pp, 3),
        "estimated_demand_impact_pct": demand_impact_pct,
        "cumulative_impact_since_2022_pct": cumulative_impact_pct,
        "transmission_lag_weeks": {"low": 24, "high": 52},
        "affected_routes": affected_routes,
        "scenario_label": scenario,
        "narrative": narrative,
    }
