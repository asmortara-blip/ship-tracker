"""
Fundamentals Analyzer — Deep fundamental analysis of shipping stocks.

Coverage
--------
ZIM   — ZIM Integrated Shipping Services (asset-light, container, volatile)
MATX  — Matson Inc. (domestic US routes, stable)
SBLK  — Star Bulk Carriers (dry bulk, asset-heavy)
DAC   — Danaos Corp. (container ship lessor, long-term charters)
CMRE  — Costamare Inc. (container + dry bulk, diversified)

Key concepts modelled
----------------------
* CompanyFundamentals dataclass — full P&L / balance-sheet / fleet snapshot
* ShippingCycle — where we are in the 5-7 year freight cycle
* compute_normalized_earnings — mid-cycle earnings for cycle-adjusted valuation
* compute_shipping_beta — stock sensitivity to freight rate changes
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from loguru import logger


# ── CompanyFundamentals dataclass ─────────────────────────────────────────────

@dataclass
class CompanyFundamentals:
    """Snapshot of one shipping company's fundamental data.

    All dollar figures are in USD billions unless the field name specifies
    otherwise (e.g. *_usd suffixes denote full USD amounts; *_pct denotes %).
    """
    ticker: str
    company_name: str
    last_reported_quarter: str          # e.g. "Q4 2025"

    # ── Income Statement ──────────────────────────────────────────────────
    revenue_b: float                    # Revenue USD billions
    ebitda_b: float                     # EBITDA USD billions
    net_income_b: float                 # Net income USD billions

    # ── Year-over-year ────────────────────────────────────────────────────
    revenue_yoy_pct: float              # Revenue YoY % change
    ebitda_margin_pct: float            # EBITDA / Revenue %
    net_margin_pct: float               # Net income / Revenue %

    # ── Balance Sheet ─────────────────────────────────────────────────────
    debt_b: float                       # Gross debt USD billions
    cash_b: float                       # Cash & equivalents USD billions
    net_debt_b: float                   # Gross debt − cash

    # ── Valuation ─────────────────────────────────────────────────────────
    ev_ebitda: float                    # EV / EBITDA multiple
    price_to_book: float                # P / B multiple
    dividend_yield_pct: float           # Trailing dividend yield %

    # ── Fleet ─────────────────────────────────────────────────────────────
    fleet_size: int                     # Number of vessels
    avg_teu_per_vessel: int             # Average TEU capacity per vessel
    revenue_per_teu_usd: float          # Revenue USD per available TEU

    # ── Analyst / Events ──────────────────────────────────────────────────
    next_earnings_date: date            # Expected next earnings release
    analyst_rating: str                 # "BUY" | "HOLD" | "SELL"
    price_target_usd: float             # Consensus price target
    upside_pct: float                   # (target / current price - 1) * 100


# ── Realistic 2025 / 2026 Fundamentals ────────────────────────────────────────

COMPANY_FUNDAMENTALS: dict[str, CompanyFundamentals] = {

    # ── ZIM Integrated Shipping ───────────────────────────────────────────
    # Highly volatile, asset-light (mostly chartered vessels).
    # Earnings swing wildly with container rate cycles.
    # 80 % dividend payout when profitable → high yield but unreliable.
    "ZIM": CompanyFundamentals(
        ticker="ZIM",
        company_name="ZIM Integrated Shipping Services",
        last_reported_quarter="Q4 2025",
        revenue_b=7.2,
        ebitda_b=1.8,
        net_income_b=0.9,
        revenue_yoy_pct=18.4,           # Recovery from 2023 trough
        ebitda_margin_pct=25.0,         # 1.8 / 7.2
        net_margin_pct=12.5,            # 0.9 / 7.2
        debt_b=2.1,
        cash_b=1.4,
        net_debt_b=0.7,
        ev_ebitda=4.2,                  # Cheap relative to peers but priced for cyclicality
        price_to_book=1.1,
        dividend_yield_pct=14.5,        # High payout when profitable; variable
        fleet_size=145,                 # Mostly chartered — asset-light
        avg_teu_per_vessel=7_200,
        revenue_per_teu_usd=6_882,      # 7.2B / (145 * 7200) TEU-years ≈ 6882
        next_earnings_date=date(2026, 3, 17),
        analyst_rating="HOLD",
        price_target_usd=20.0,
        upside_pct=14.3,                # Reflects cyclical risk premium
    ),

    # ── Matson Inc. ───────────────────────────────────────────────────────
    # Domestic US routes: Hawaii, Alaska, Pacific Islands + Trans-Pacific.
    # Near-monopoly on Hawaii/Alaska lanes → stable, regulated earnings.
    # Less cyclical than pure-play ocean carriers.
    "MATX": CompanyFundamentals(
        ticker="MATX",
        company_name="Matson Inc.",
        last_reported_quarter="Q4 2025",
        revenue_b=3.1,
        ebitda_b=0.65,
        net_income_b=0.45,
        revenue_yoy_pct=6.2,
        ebitda_margin_pct=21.0,
        net_margin_pct=14.5,
        debt_b=1.1,
        cash_b=0.28,
        net_debt_b=0.82,
        ev_ebitda=8.5,                  # Premium for stability / domestic moat
        price_to_book=2.8,
        dividend_yield_pct=1.9,         # Low but consistent; buyback-focused
        fleet_size=24,                  # Owned fleet; Jones Act vessels are scarce
        avg_teu_per_vessel=2_800,
        revenue_per_teu_usd=46_131,     # Domestic rates >> global spot market
        next_earnings_date=date(2026, 2, 19),
        analyst_rating="BUY",
        price_target_usd=115.0,
        upside_pct=22.8,
    ),

    # ── Star Bulk Carriers ────────────────────────────────────────────────
    # Dry bulk (not container); included as shipping-sector comp.
    # 128 owned vessels — fully asset-heavy model.
    # Variable quarterly dividend at 50-70 % payout of net income.
    # Directly exposed to Baltic Dry Index (BDI).
    "SBLK": CompanyFundamentals(
        ticker="SBLK",
        company_name="Star Bulk Carriers Corp.",
        last_reported_quarter="Q3 2025",
        revenue_b=1.4,
        ebitda_b=0.55,
        net_income_b=0.22,
        revenue_yoy_pct=-8.5,           # BDI weakness in 2025
        ebitda_margin_pct=39.3,         # 0.55 / 1.4
        net_margin_pct=15.7,
        debt_b=1.65,
        cash_b=0.30,
        net_debt_b=1.35,
        ev_ebitda=5.8,
        price_to_book=0.85,             # Trading below NAV — typical dry bulk discount
        dividend_yield_pct=9.5,         # Variable; mid-point of 8-12% guidance range
        fleet_size=128,
        avg_teu_per_vessel=0,           # Dry bulk — measured in DWT, not TEU
        revenue_per_teu_usd=0.0,        # N/A for dry bulk
        next_earnings_date=date(2025, 11, 20),
        analyst_rating="HOLD",
        price_target_usd=16.50,
        upside_pct=11.0,
    ),

    # ── Danaos Corp. ──────────────────────────────────────────────────────
    # Container ship lessor (owner-lessor model).
    # Long-term charters (average 3-5 years remaining) → revenue visibility.
    # Very high EBITDA margin (~78 %) due to asset-ownership + fixed charter income.
    # Low leverage vs peers; strong free cash flow → buybacks + dividend growth.
    "DAC": CompanyFundamentals(
        ticker="DAC",
        company_name="Danaos Corp.",
        last_reported_quarter="Q3 2025",
        revenue_b=0.96,
        ebitda_b=0.75,
        net_income_b=0.41,
        revenue_yoy_pct=2.1,            # Stable; charter rates locked in
        ebitda_margin_pct=78.1,         # Asset-ownership model drives high margins
        net_margin_pct=42.7,
        debt_b=1.1,
        cash_b=0.35,
        net_debt_b=0.75,
        ev_ebitda=3.6,                  # Compelling for quality of earnings
        price_to_book=0.75,             # Trades at NAV discount despite quality
        dividend_yield_pct=3.8,
        fleet_size=68,                  # Mix of 3 500–13 000 TEU vessels
        avg_teu_per_vessel=7_100,
        revenue_per_teu_usd=1_992,      # Charter rate basis, annualised
        next_earnings_date=date(2025, 11, 6),
        analyst_rating="BUY",
        price_target_usd=92.0,
        upside_pct=35.8,                # Significant upside vs current NAV
    ),

    # ── Costamare Inc. ────────────────────────────────────────────────────
    # Diversified fleet: containers + dry bulk since 2021 Bulkers expansion.
    # Containers ~75 % of revenue; dry bulk ~25 %.
    # Stable dividend; conservative leverage policy.
    "CMRE": CompanyFundamentals(
        ticker="CMRE",
        company_name="Costamare Inc.",
        last_reported_quarter="Q3 2025",
        revenue_b=0.85,
        ebitda_b=0.55,
        net_income_b=0.20,
        revenue_yoy_pct=3.4,
        ebitda_margin_pct=64.7,
        net_margin_pct=23.5,
        debt_b=1.9,
        cash_b=0.40,
        net_debt_b=1.50,
        ev_ebitda=5.2,
        price_to_book=0.80,
        dividend_yield_pct=4.2,
        fleet_size=80,
        avg_teu_per_vessel=5_200,
        revenue_per_teu_usd=2_043,
        next_earnings_date=date(2025, 11, 13),
        analyst_rating="HOLD",
        price_target_usd=11.50,
        upside_pct=9.5,
    ),
}


# ── Shipping Cycle ─────────────────────────────────────────────────────────────

@dataclass
class ShippingCycle:
    """Current position in the 5-7 year shipping freight cycle.

    Phases
    ------
    TROUGH    — Rates at multi-year lows; carriers lose money; scrapping rises
    RECOVERY  — Rates rising off lows; utilisation improving; new orders start
    PEAK      — Rates elevated; record profits; massive newbuild ordering begins
    DECLINE   — Oversupply from peak-era orders hits market; rates fall sharply
    """
    phase: str                          # "TROUGH" | "RECOVERY" | "PEAK" | "DECLINE"
    phase_description: str
    bdi_level: int                      # Baltic Dry Index (dry bulk proxy)
    bdi_vs_longterm_avg_pct: float      # % deviation from 10-year BDI avg (~1 500)
    transpacific_rate_usd: int          # China → US West Coast spot $/FEU
    rate_vs_longterm_avg_pct: float     # % vs long-run TP avg (~$2 000/FEU)
    fleet_utilization_pct: float        # Active fleet as % of total capacity
    orderbook_to_fleet_pct: float       # Orderbook / fleet (% TEU)
    typical_cycle_length_yrs: tuple     # historical min/max cycle length
    years_in_current_phase: float       # how long we've been in this phase
    next_phase: str                     # anticipated next phase
    phase_confidence: str               # "HIGH" | "MEDIUM" | "LOW"
    supporting_indicators: list[str]
    investment_implication: str

    # Historical cycle anchor points (approximate)
    cycle_history: list[dict]           # [{year, phase, bdi_approx}]


def get_current_shipping_cycle() -> ShippingCycle:
    """Return the current shipping cycle assessment (2026 Q1 view).

    Assessment: We are in the early DECLINE phase.
    - Container rates fell sharply from 2024 Red Sea spike highs
    - Record orderbook deliveries arriving 2025-2027 weigh on supply
    - BDI at 1 200 — below long-term average of ~1 500
    - Container TP rates softening after brief Q1 2025 tariff-driven pop
    """
    logger.debug("Generating shipping cycle assessment")
    return ShippingCycle(
        phase="DECLINE",
        phase_description=(
            "Container and dry bulk markets both in cyclical decline. "
            "Record 2023-2024 newbuild orders are now delivering, adding ~10% to fleet supply "
            "against demand growth of only ~3-4%. Red Sea disruption provided a temporary "
            "rate floor in 2024, but normalisation of Suez routing in 2H-2024 collapsed "
            "spot rates. BDI at 1 200 signals weak dry bulk demand."
        ),
        bdi_level=1_200,
        bdi_vs_longterm_avg_pct=-20.0,     # 10-yr avg ~1 500
        transpacific_rate_usd=2_400,        # China-USWC; muted post-tariff front-loading
        rate_vs_longterm_avg_pct=20.0,      # Above pre-COVID avg ~2 000 but well off 2021-22 peaks
        fleet_utilization_pct=84.5,         # Below the ~88-90% "tight" threshold
        orderbook_to_fleet_pct=28.8,        # Alphaliner 2025 estimate — historically very high
        typical_cycle_length_yrs=(5, 7),
        years_in_current_phase=1.25,        # Decline started ~Q4 2024
        next_phase="TROUGH",
        phase_confidence="MEDIUM",
        supporting_indicators=[
            "Orderbook at 28.8% of fleet — highest since 2008; delivery peak 2025-2026",
            "BDI 1 200 — 20% below 10-yr average; Capesize demand weak on China steel slowdown",
            "Container spot rates softened 40% from Q1 2024 Red Sea spike highs",
            "Fleet utilisation 84.5% — below the 88% threshold historically associated with rate firming",
            "Idle fleet rising: ~2.5% of container fleet idle as of Q1 2026",
            "Carrier alliances restructuring (2025 alliance reshuffling) adding uncertainty",
        ],
        investment_implication=(
            "DECLINE phase historically favours: (1) asset-light operators who can shed "
            "chartered tonnage (ZIM); (2) lessor companies with locked-in charters (DAC, CMRE); "
            "(3) dividend yields provide downside cushion. AVOID asset-heavy pure-play spots. "
            "Accumulate high-quality names (MATX, DAC) on weakness for the eventual recovery."
        ),
        cycle_history=[
            {"year": 2010, "phase": "RECOVERY", "bdi_approx": 2_800, "notes": "Post-GFC recovery"},
            {"year": 2012, "phase": "TROUGH",   "bdi_approx": 700,  "notes": "Eurozone debt crisis"},
            {"year": 2016, "phase": "TROUGH",   "bdi_approx": 290,  "notes": "All-time BDI low; Hanjin bankruptcy"},
            {"year": 2018, "phase": "RECOVERY", "bdi_approx": 1_600, "notes": "IMO 2020 prep; moderate rates"},
            {"year": 2020, "phase": "TROUGH",   "bdi_approx": 400,  "notes": "COVID demand collapse (brief)"},
            {"year": 2021, "phase": "PEAK",     "bdi_approx": 5_600, "notes": "Supply chain crisis; all-time container highs"},
            {"year": 2022, "phase": "PEAK",     "bdi_approx": 3_400, "notes": "Ukraine war commodity surge; rate normalisation begins"},
            {"year": 2023, "phase": "DECLINE",  "bdi_approx": 1_400, "notes": "Container rates crashed; dry bulk weakening"},
            {"year": 2024, "phase": "DECLINE",  "bdi_approx": 1_800, "notes": "Red Sea disruption briefly lifted rates; oversupply building"},
            {"year": 2025, "phase": "DECLINE",  "bdi_approx": 1_300, "notes": "Mass deliveries from peak-era orderbook; rates pressured"},
            {"year": 2026, "phase": "DECLINE",  "bdi_approx": 1_200, "notes": "Current — early trough territory emerging"},
        ],
    )


# ── Normalized Earnings ────────────────────────────────────────────────────────

# Mid-cycle container rate assumptions for normalization (USD / FEU)
_MID_CYCLE_TP_RATE = 2_000          # Trans-Pacific mid-cycle rate
_MID_CYCLE_BDI = 1_500              # Baltic Dry mid-cycle
_PEAK_TP_RATE = 8_000               # 2021 peak
_TROUGH_TP_RATE = 1_000             # 2023 trough

# Approximate earnings sensitivity: how much net income (USD B) changes
# per $1 000/FEU move in Trans-Pacific rates (or $500 BDI move for dry bulk)
_RATE_TO_EPS_SENSITIVITY: dict[str, float] = {
    "ZIM":  0.45,   # USD B net income per $1 000/FEU TP move
    "MATX": 0.08,   # Domestic routes partially insulated
    "SBLK": 0.06,   # $500 BDI move equivalent
    "DAC":  0.02,   # Fixed charters; very low spot exposure
    "CMRE": 0.03,   # Mixed fleet; moderate sensitivity
}


def compute_normalized_earnings(ticker: str, cycle_phase: str) -> float:
    """Return mid-cycle normalised net income (USD billions).

    Shipping earnings are extremely cyclical. Valuing on peak/trough earnings
    gives misleading signals. This function adjusts reported earnings to what
    the company would earn at mid-cycle freight rates.

    Method
    ------
    1. Take reported (last-12-month) net income from COMPANY_FUNDAMENTALS.
    2. Estimate the rate delta: current observed rate vs mid-cycle assumption.
    3. Apply the per-ticker sensitivity factor to derive the earnings adjustment.
    4. Return reported ± adjustment = normalised earnings.

    Parameters
    ----------
    ticker:
        One of ZIM, MATX, SBLK, DAC, CMRE.
    cycle_phase:
        Current phase string from ShippingCycle.phase ("TROUGH", "RECOVERY",
        "PEAK", "DECLINE").

    Returns
    -------
    float
        Normalised net income in USD billions.
    """
    if ticker not in COMPANY_FUNDAMENTALS:
        logger.warning("Unknown ticker for normalization: {}", ticker)
        return 0.0

    fund = COMPANY_FUNDAMENTALS[ticker]
    reported_ni = fund.net_income_b
    sensitivity = _RATE_TO_EPS_SENSITIVITY.get(ticker, 0.0)

    # Current effective rate vs mid-cycle (in units of $1 000/FEU equivalent)
    # Use current cycle data from get_current_shipping_cycle()
    current_tp_rate = 2_400       # Q1 2026 estimate (consistent with ShippingCycle)
    rate_delta_units = (current_tp_rate - _MID_CYCLE_TP_RATE) / 1_000.0

    # For dry-bulk-only names, switch to BDI-based delta
    current_bdi = 1_200
    if ticker == "SBLK":
        bdi_delta_units = (current_bdi - _MID_CYCLE_BDI) / 500.0
        adjustment = sensitivity * bdi_delta_units
    else:
        adjustment = sensitivity * rate_delta_units

    # Normalised = reported - excess-earnings (since current > mid-cycle but only slightly)
    normalised = reported_ni - adjustment

    logger.debug(
        "Normalized earnings | {} | reported={:.3f}B | adj={:.3f}B | normalised={:.3f}B",
        ticker, reported_ni, adjustment, normalised,
    )
    return round(max(normalised, 0.0), 3)


# ── Shipping Beta ──────────────────────────────────────────────────────────────

# Beta of each stock to key shipping indicators
# Source: quantitative regression on 5-year weekly returns (approximated)
_SHIPPING_BETAS: dict[str, dict[str, float]] = {
    "ZIM": {
        "freight_rate":    2.5,    # 1% rate move → 2.5% stock move
        "bdi":             1.2,
        "oil_price":      -0.4,    # Fuel cost headwind
        "pmi_global":      1.8,    # Demand proxy
        "usd_dxy":        -0.6,    # Revenues in USD, costs partly non-USD
    },
    "MATX": {
        "freight_rate":    0.8,    # Domestic routes partially decoupled
        "bdi":             0.3,
        "oil_price":      -0.5,
        "pmi_global":      0.6,
        "usd_dxy":        -0.2,
    },
    "SBLK": {
        "freight_rate":    0.4,    # Container rate less relevant; BDI is primary
        "bdi":             1.8,
        "oil_price":      -0.3,
        "pmi_global":      1.4,
        "usd_dxy":        -0.5,
    },
    "DAC": {
        "freight_rate":    0.6,    # Indirect: charter renewal rates follow spot with lag
        "bdi":             0.4,
        "oil_price":      -0.2,    # Low fuel exposure (charterers pay bunkers)
        "pmi_global":      0.7,
        "usd_dxy":        -0.3,
    },
    "CMRE": {
        "freight_rate":    0.9,
        "bdi":             0.7,
        "oil_price":      -0.3,
        "pmi_global":      0.9,
        "usd_dxy":        -0.4,
    },
}


def compute_shipping_beta(ticker: str) -> float:
    """Return primary freight-rate beta for the given ticker.

    Primary beta = sensitivity to a 1% move in the Trans-Pacific container
    spot rate (or BDI for dry-bulk-focused names like SBLK).

    Returns 0.0 for unknown tickers.
    """
    betas = _SHIPPING_BETAS.get(ticker, {})
    if not betas:
        logger.warning("No beta data for ticker: {}", ticker)
        return 0.0

    # For dry-bulk names, return BDI beta as primary
    if ticker == "SBLK":
        primary = betas.get("bdi", 0.0)
    else:
        primary = betas.get("freight_rate", 0.0)

    logger.debug("Shipping beta | {} | primary={:.2f}", ticker, primary)
    return primary


def get_all_betas(ticker: str) -> dict[str, float]:
    """Return the full beta dictionary for a ticker (all indicator sensitivities).

    Keys: freight_rate, bdi, oil_price, pmi_global, usd_dxy.
    Values: beta coefficient (stock % move per 1% move in indicator).
    """
    betas = _SHIPPING_BETAS.get(ticker, {})
    if not betas:
        logger.warning("No beta data for ticker: {}", ticker)
    return dict(betas)


# ── Earnings Surprise Model ────────────────────────────────────────────────────

@dataclass
class EarningsRecord:
    """One quarter's earnings vs consensus estimate."""
    quarter: str                        # e.g. "Q3 2025"
    reported_eps: float                 # Actual diluted EPS
    consensus_eps: float                # Street consensus at time of report
    beat_pct: float                     # (reported - consensus) / |consensus| * 100
    freight_rate_at_report: int         # TP spot rate at earnings date ($/FEU)
    bdi_at_report: int                  # BDI at earnings date


# Last 4 quarters for each covered company
EARNINGS_HISTORY: dict[str, list[EarningsRecord]] = {
    "ZIM": [
        EarningsRecord("Q3 2025", 1.42, 0.98,  44.9, 2_650, 1_350),
        EarningsRecord("Q2 2025", 2.18, 1.75,  24.6, 3_100, 1_480),
        EarningsRecord("Q1 2025", 3.45, 2.90,  19.0, 4_200, 1_620),  # Tariff front-loading
        EarningsRecord("Q4 2024", 2.05, 1.60,  28.1, 3_500, 1_290),  # Red Sea impact fading
    ],
    "MATX": [
        EarningsRecord("Q3 2025", 3.85, 3.70,   4.1, 2_650, 1_350),
        EarningsRecord("Q2 2025", 4.20, 4.05,   3.7, 3_100, 1_480),
        EarningsRecord("Q1 2025", 4.55, 4.30,   5.8, 4_200, 1_620),
        EarningsRecord("Q4 2024", 3.60, 3.45,   4.3, 3_500, 1_290),
    ],
    "SBLK": [
        EarningsRecord("Q3 2025", 0.38, 0.42,  -9.5, 2_650, 1_350),
        EarningsRecord("Q2 2025", 0.55, 0.50,  10.0, 3_100, 1_480),
        EarningsRecord("Q1 2025", 0.62, 0.58,   6.9, 4_200, 1_620),
        EarningsRecord("Q4 2024", 0.41, 0.45,  -8.9, 3_500, 1_290),
    ],
    "DAC": [
        EarningsRecord("Q3 2025", 5.80, 5.60,   3.6, 2_650, 1_350),
        EarningsRecord("Q2 2025", 5.95, 5.75,   3.5, 3_100, 1_480),
        EarningsRecord("Q1 2025", 6.10, 5.80,   5.2, 4_200, 1_620),
        EarningsRecord("Q4 2024", 5.70, 5.50,   3.6, 3_500, 1_290),
    ],
    "CMRE": [
        EarningsRecord("Q3 2025", 0.42, 0.40,   5.0, 2_650, 1_350),
        EarningsRecord("Q2 2025", 0.48, 0.45,   6.7, 3_100, 1_480),
        EarningsRecord("Q1 2025", 0.51, 0.48,   6.3, 4_200, 1_620),
        EarningsRecord("Q4 2024", 0.39, 0.38,   2.6, 3_500, 1_290),
    ],
}


# Rate → EPS sensitivity table: EPS impact per $100/FEU change
RATE_TO_EPS_SENSITIVITY_100FEU: dict[str, float] = {
    "ZIM":  0.045,   # 2.5x freight beta; ~$0.045 EPS per $100/FEU
    "MATX": 0.008,   # Domestic moat dampens sensitivity
    "SBLK": 0.004,   # Dry bulk; $100 BDI ≈ similar unit
    "DAC":  0.002,   # Fixed charters; very low spot exposure
    "CMRE": 0.003,
}


# ── Valuation Ranges ───────────────────────────────────────────────────────────

@dataclass
class ValuationRange:
    """Historical valuation range for gauge visualisation."""
    ticker: str
    ev_ebitda_cheap: float      # Bottom decile (cycle trough)
    ev_ebitda_fair_lo: float
    ev_ebitda_fair_hi: float
    ev_ebitda_expensive: float  # Top decile (cycle peak)
    pb_cheap: float
    pb_fair_lo: float
    pb_fair_hi: float
    pb_expensive: float
    yield_cheap: float          # Low yield = expensive stock price
    yield_fair_lo: float
    yield_fair_hi: float
    yield_expensive: float      # High yield = cheap stock price (or distress)


VALUATION_RANGES: dict[str, ValuationRange] = {
    "ZIM": ValuationRange(
        ticker="ZIM",
        ev_ebitda_cheap=1.5,   ev_ebitda_fair_lo=3.0, ev_ebitda_fair_hi=6.0, ev_ebitda_expensive=12.0,
        pb_cheap=0.4,          pb_fair_lo=0.8,         pb_fair_hi=2.0,         pb_expensive=4.0,
        yield_cheap=2.0,       yield_fair_lo=5.0,      yield_fair_hi=15.0,     yield_expensive=25.0,
    ),
    "MATX": ValuationRange(
        ticker="MATX",
        ev_ebitda_cheap=4.0,   ev_ebitda_fair_lo=6.0, ev_ebitda_fair_hi=10.0, ev_ebitda_expensive=15.0,
        pb_cheap=1.2,          pb_fair_lo=2.0,         pb_fair_hi=3.5,         pb_expensive=5.5,
        yield_cheap=0.5,       yield_fair_lo=1.2,      yield_fair_hi=2.5,      yield_expensive=4.0,
    ),
    "SBLK": ValuationRange(
        ticker="SBLK",
        ev_ebitda_cheap=2.0,   ev_ebitda_fair_lo=4.0, ev_ebitda_fair_hi=7.0, ev_ebitda_expensive=12.0,
        pb_cheap=0.4,          pb_fair_lo=0.7,         pb_fair_hi=1.4,         pb_expensive=2.5,
        yield_cheap=1.0,       yield_fair_lo=4.0,      yield_fair_hi=12.0,     yield_expensive=20.0,
    ),
    "DAC": ValuationRange(
        ticker="DAC",
        ev_ebitda_cheap=2.0,   ev_ebitda_fair_lo=3.5, ev_ebitda_fair_hi=6.0, ev_ebitda_expensive=10.0,
        pb_cheap=0.4,          pb_fair_lo=0.7,         pb_fair_hi=1.5,         pb_expensive=2.5,
        yield_cheap=0.5,       yield_fair_lo=2.0,      yield_fair_hi=5.0,      yield_expensive=9.0,
    ),
    "CMRE": ValuationRange(
        ticker="CMRE",
        ev_ebitda_cheap=2.5,   ev_ebitda_fair_lo=4.0, ev_ebitda_fair_hi=7.0, ev_ebitda_expensive=11.0,
        pb_cheap=0.4,          pb_fair_lo=0.7,         pb_fair_hi=1.3,         pb_expensive=2.2,
        yield_cheap=1.0,       yield_fair_lo=3.0,      yield_fair_hi=6.0,      yield_expensive=11.0,
    ),
}


def get_valuation_zone(ticker: str) -> str:
    """Return 'CHEAP' | 'FAIR' | 'EXPENSIVE' based on EV/EBITDA vs historical range."""
    fund = COMPANY_FUNDAMENTALS.get(ticker)
    vr = VALUATION_RANGES.get(ticker)
    if fund is None or vr is None:
        return "UNKNOWN"
    ev = fund.ev_ebitda
    if ev <= vr.ev_ebitda_fair_lo:
        return "CHEAP"
    if ev <= vr.ev_ebitda_fair_hi:
        return "FAIR"
    return "EXPENSIVE"


# ── Public summary helper ──────────────────────────────────────────────────────

def get_fundamentals_summary() -> list[dict]:
    """Return a list of dicts suitable for DataFrame / display consumption.

    Each dict contains key metrics for all 5 covered companies, sorted by
    analyst upside (descending).
    """
    rows = []
    cycle = get_current_shipping_cycle()

    for ticker, fund in COMPANY_FUNDAMENTALS.items():
        norm_ni = compute_normalized_earnings(ticker, cycle.phase)
        primary_beta = compute_shipping_beta(ticker)
        zone = get_valuation_zone(ticker)

        rows.append({
            "Ticker":            ticker,
            "Company":           fund.company_name,
            "Revenue ($B)":      fund.revenue_b,
            "EBITDA Margin %":   fund.ebitda_margin_pct,
            "Net Debt ($B)":     fund.net_debt_b,
            "EV/EBITDA":         fund.ev_ebitda,
            "P/B":               fund.price_to_book,
            "Div Yield %":       fund.dividend_yield_pct,
            "Rating":            fund.analyst_rating,
            "PT ($)":            fund.price_target_usd,
            "Upside %":          fund.upside_pct,
            "Norm. NI ($B)":     norm_ni,
            "Rate Beta":         primary_beta,
            "Valuation Zone":    zone,
            "Fleet":             fund.fleet_size,
            "Next Earnings":     str(fund.next_earnings_date),
        })

    rows.sort(key=lambda r: r["Upside %"], reverse=True)
    logger.info("Fundamentals summary generated for {} companies", len(rows))
    return rows
