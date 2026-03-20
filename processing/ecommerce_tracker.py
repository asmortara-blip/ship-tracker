"""ecommerce_tracker.py — E-commerce demand signals for container shipping.

E-commerce has fundamentally changed container shipping demand patterns.
Major platforms drive discrete, calendar-anchored demand spikes that
importers and freight buyers must anticipate weeks or months in advance.

Key dynamics covered:
- Amazon Prime Day / Holiday Q4 trans-Pacific spikes
- SHEIN / TEMU direct-to-consumer air freight disruption
- Alibaba 11.11 Singles Day (largest shipping event globally)
- Shopify distributed merchant peak (Black Friday / Cyber Monday)
- De minimis $800 threshold and policy risk
- Retail calendar booking windows for container procurement
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from loguru import logger


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class EcommerceSignal:
    """A single e-commerce platform demand signal for container shipping."""

    platform: str                   # "AMAZON" | "ALIBABA" | "SHEIN" | "TEMU" | "SHOPIFY" | "WAYFAIR"
    metric_name: str                # Human-readable metric label
    current_value: float            # Numeric value of the metric
    yoy_growth_pct: float           # Year-over-year growth in percent
    shipping_implication: str       # Plain-language shipping impact description
    affected_routes: list[str]      # Route keys, e.g. ["transpacific_eb", "asia_europe"]
    lead_time_weeks: int            # Weeks ahead this signal precedes actual shipping demand
    confidence: float               # [0, 1] confidence in the signal


@dataclass
class RetailCalendar:
    """A recurring retail event that drives container booking demand."""

    event_name: str
    month: int                                   # Calendar month of the retail event itself
    typical_order_window_weeks_before: int       # Weeks before event when containers are booked
    container_demand_multiplier: float           # 1.0 = baseline; 1.35 = +35% demand
    affected_routes: list[str]
    description: str = ""
    day: int = 1                                 # Approximate day within month


# ── ECOMMERCE_SIGNALS — 2025/2026 platform data ───────────────────────────────

ECOMMERCE_SIGNALS: dict[str, list[EcommerceSignal]] = {

    "AMAZON": [
        EcommerceSignal(
            platform="AMAZON",
            metric_name="China-origin sourcing share",
            current_value=35.0,
            yoy_growth_pct=-3.5,
            shipping_implication=(
                "~35% of Amazon US imports still source from China despite tariff pressure. "
                "Gradual diversification to Vietnam and Mexico reducing trans-Pacific concentration."
            ),
            affected_routes=["transpacific_eb", "us_mexico"],
            lead_time_weeks=8,
            confidence=0.82,
        ),
        EcommerceSignal(
            platform="AMAZON",
            metric_name="Prime Day trans-Pacific spot orders (May-June)",
            current_value=40.0,
            yoy_growth_pct=8.0,
            shipping_implication=(
                "Prime Day (typically July) drives a +40% spike in trans-Pacific spot freight "
                "bookings in May-June as sellers replenish FBA warehouse inventory ahead of "
                "the sale event. Rates typically surge 15-25% in this window."
            ),
            affected_routes=["transpacific_eb"],
            lead_time_weeks=6,
            confidence=0.88,
        ),
        EcommerceSignal(
            platform="AMAZON",
            metric_name="Q4 holiday capacity demand lift",
            current_value=25.0,
            yoy_growth_pct=5.0,
            shipping_implication=(
                "Holiday season drives +25% capacity demand on trans-Pacific EB. "
                "Amazon's forward-stocking model means peak ocean freight occurs "
                "July-September for December retail. Booking window: August is critical."
            ),
            affected_routes=["transpacific_eb", "asia_europe"],
            lead_time_weeks=16,
            confidence=0.91,
        ),
        EcommerceSignal(
            platform="AMAZON",
            metric_name="Mexico nearshoring sourcing increase",
            current_value=15.0,
            yoy_growth_pct=28.0,
            shipping_implication=(
                "Nearshoring to Mexico accelerating — +15% Mexico sourcing replacing "
                "China-origin goods. Boosts US-Mexico cross-border and Gulf Coast port volumes. "
                "Structural multi-year trend reducing China trans-Pacific dependency."
            ),
            affected_routes=["us_mexico", "gulf_coast_inbound"],
            lead_time_weeks=4,
            confidence=0.75,
        ),
    ],

    "SHEIN": [
        EcommerceSignal(
            platform="SHEIN",
            metric_name="YoY volume growth (2024)",
            current_value=200.0,
            yoy_growth_pct=200.0,
            shipping_implication=(
                "SHEIN volume grew 200%+ YoY in 2024. Ultra-fast fashion direct-to-consumer "
                "model relies heavily on air freight for speed-to-consumer, cannibalizing "
                "lower-value ocean container demand on Asia-US lanes."
            ),
            affected_routes=["transpacific_eb", "asia_europe"],
            lead_time_weeks=1,
            confidence=0.78,
        ),
        EcommerceSignal(
            platform="SHEIN",
            metric_name="Air freight share of shipments",
            current_value=80.0,
            yoy_growth_pct=15.0,
            shipping_implication=(
                "~80% of SHEIN shipments move by air (direct parcel), leveraging de minimis "
                "exemption. Container ocean freight used primarily for bulk basics replenishment "
                "to bonded warehouses. Policy risk: de minimis reform could shift 10-15% back to ocean."
            ),
            affected_routes=["transpacific_eb"],
            lead_time_weeks=0,
            confidence=0.80,
        ),
        EcommerceSignal(
            platform="SHEIN",
            metric_name="De minimis parcel volume (monthly, millions)",
            current_value=30.0,
            yoy_growth_pct=180.0,
            shipping_implication=(
                "SHEIN exploits the $800 de minimis threshold — packages under $800 enter "
                "the US duty-free. Congressional pressure to remove threshold could add "
                "$15-20B in tariff costs and force partial pivot back to ocean freight."
            ),
            affected_routes=["transpacific_eb", "asia_europe"],
            lead_time_weeks=2,
            confidence=0.72,
        ),
    ],

    "TEMU": [
        EcommerceSignal(
            platform="TEMU",
            metric_name="YoY volume growth (2024)",
            current_value=250.0,
            yoy_growth_pct=250.0,
            shipping_implication=(
                "TEMU volume surged 250%+ YoY in 2024 with an aggressive US market push. "
                "Direct-from-factory model similar to SHEIN — predominantly air freight. "
                "Straining air cargo capacity on PVG/CAN-US lanes."
            ),
            affected_routes=["transpacific_eb"],
            lead_time_weeks=1,
            confidence=0.76,
        ),
        EcommerceSignal(
            platform="TEMU",
            metric_name="De minimis reliance (%)",
            current_value=85.0,
            yoy_growth_pct=20.0,
            shipping_implication=(
                "TEMU is even more dependent on de minimis than SHEIN (~85% of packages). "
                "Executive Order threat to close loophole represents existential regulatory "
                "risk; ocean container demand would surge if duty-free air parcel route closes."
            ),
            affected_routes=["transpacific_eb"],
            lead_time_weeks=0,
            confidence=0.70,
        ),
    ],

    "ALIBABA": [
        EcommerceSignal(
            platform="ALIBABA",
            metric_name="11.11 Singles Day packages (Nov 2024, billions)",
            current_value=1.3,
            yoy_growth_pct=12.0,
            shipping_implication=(
                "Alibaba's 11.11 Singles Day is the largest single shipping event globally — "
                "1.3 billion packages in November 2024. Trans-Pacific and Asia-Europe "
                "container bookings spike +35% in Sept-Oct as merchants pre-position inventory."
            ),
            affected_routes=["transpacific_eb", "asia_europe", "intra_asia_sea"],
            lead_time_weeks=8,
            confidence=0.92,
        ),
        EcommerceSignal(
            platform="ALIBABA",
            metric_name="AliExpress cross-border GMV growth (%)",
            current_value=28.0,
            yoy_growth_pct=28.0,
            shipping_implication=(
                "AliExpress China-to-everywhere model expanding rapidly. Drives "
                "consistent baseline container demand across Asia-Europe, Asia-LATAM, "
                "and Asia-Africa lanes year-round with large event spikes around 11.11."
            ),
            affected_routes=["asia_europe", "transpacific_eb", "asia_latam"],
            lead_time_weeks=6,
            confidence=0.80,
        ),
    ],

    "SHOPIFY": [
        EcommerceSignal(
            platform="SHOPIFY",
            metric_name="Black Friday / Cyber Monday merchant peak demand",
            current_value=35.0,
            yoy_growth_pct=10.0,
            shipping_implication=(
                "Shopify's distributed merchant base creates a broad peak in Nov-Dec. "
                "Merchants book containers in Aug-Sept to ensure inventory arrives "
                "before Black Friday. Trans-Pacific EB demand lifts +20-25% Aug-Sept."
            ),
            affected_routes=["transpacific_eb", "asia_europe"],
            lead_time_weeks=12,
            confidence=0.84,
        ),
        EcommerceSignal(
            platform="SHOPIFY",
            metric_name="International merchant cross-border growth (%)",
            current_value=22.0,
            yoy_growth_pct=22.0,
            shipping_implication=(
                "Shopify's international merchant growth drives incremental cross-border "
                "ocean shipments, particularly Asia-Europe and trans-Pacific WB. "
                "DTC brands increasingly using LCL consolidation for smaller volumes."
            ),
            affected_routes=["transpacific_eb", "transpacific_wb", "asia_europe"],
            lead_time_weeks=10,
            confidence=0.70,
        ),
    ],

    "WAYFAIR": [
        EcommerceSignal(
            platform="WAYFAIR",
            metric_name="Furniture / home goods import volume growth (%)",
            current_value=18.0,
            yoy_growth_pct=6.0,
            shipping_implication=(
                "Wayfair drives high-cube container demand for bulky home goods from "
                "China, Vietnam, and Malaysia. Peak booking period: April-June for "
                "summer home improvement season. Sensitive to housing market cycles."
            ),
            affected_routes=["transpacific_eb", "asia_europe"],
            lead_time_weeks=10,
            confidence=0.68,
        ),
    ],
}


# ── RETAIL_CALENDAR — shipping demand calendar ────────────────────────────────

RETAIL_CALENDAR: list[RetailCalendar] = [
    RetailCalendar(
        event_name="Chinese New Year",
        month=2,
        day=10,
        typical_order_window_weeks_before=6,
        container_demand_multiplier=1.30,
        affected_routes=["transpacific_eb", "asia_europe", "intra_asia_sea"],
        description=(
            "Chinese factories close 2-4 weeks. Exporters rush shipments in December-January "
            "before shutdown. Book containers in December or accept post-CNY delays. "
            "Post-CNY: 2-3 week export drought, then rapid catch-up demand."
        ),
    ),
    RetailCalendar(
        event_name="Easter / European Spring Peak",
        month=4,
        day=1,
        typical_order_window_weeks_before=8,
        container_demand_multiplier=1.10,
        affected_routes=["asia_europe", "transpacific_eb"],
        description=(
            "European retail spring peak driven by Easter and spring fashion season. "
            "Asia-Europe bookings lift ~10% in January-February for April arrival. "
            "Fashion, electronics, and consumer goods primary cargo types."
        ),
    ),
    RetailCalendar(
        event_name="Back to School",
        month=8,
        day=15,
        typical_order_window_weeks_before=10,
        container_demand_multiplier=1.20,
        affected_routes=["transpacific_eb"],
        description=(
            "Back-to-school season (August-September) drives apparel, electronics, "
            "and school supplies imports. Book containers May-June for August arrival. "
            "Second-largest US retail event; significant trans-Pacific EB demand."
        ),
    ),
    RetailCalendar(
        event_name="Summer Inventory Build",
        month=7,
        day=1,
        typical_order_window_weeks_before=8,
        container_demand_multiplier=1.35,
        affected_routes=["transpacific_eb", "asia_europe"],
        description=(
            "The trans-Pacific peak season runs July-September as US retailers stock "
            "for fall and holiday. Historically the highest-rate period of the year. "
            "Book in May for July delivery. Spot rates typically +20-40% above Q1 levels."
        ),
    ),
    RetailCalendar(
        event_name="Singles Day (11.11)",
        month=11,
        day=11,
        typical_order_window_weeks_before=8,
        container_demand_multiplier=1.35,
        affected_routes=["transpacific_eb", "asia_europe", "intra_asia_sea"],
        description=(
            "Alibaba's Singles Day (November 11) is the world's largest single shopping event. "
            "1.3 billion packages in 2024. Trans-Pacific bookings spike +35% in September-October. "
            "Affects intra-Asia routes as well as China-to-US and China-to-Europe lanes."
        ),
    ),
    RetailCalendar(
        event_name="Black Friday / Cyber Monday",
        month=11,
        day=28,
        typical_order_window_weeks_before=13,
        container_demand_multiplier=1.40,
        affected_routes=["transpacific_eb", "asia_europe"],
        description=(
            "Combined Black Friday and Cyber Monday is the peak US retail event. "
            "Container bookings peak August-September for November arrival. "
            "Amazon, Shopify merchants, and big-box retailers all competing for capacity. "
            "Trans-Pacific EB demand lifts +40% vs baseline during booking window."
        ),
    ),
    RetailCalendar(
        event_name="Christmas / Holiday Peak",
        month=12,
        day=20,
        typical_order_window_weeks_before=18,
        container_demand_multiplier=1.45,
        affected_routes=["transpacific_eb", "asia_europe"],
        description=(
            "Christmas is the largest single demand driver for trans-Pacific EB containers. "
            "Book in July-August for December store arrival (allowing for transit + warehouse time). "
            "Holiday inventory window for e-commerce typically closes by end of September. "
            "Demand multiplier of 1.45x — highest of the retail calendar."
        ),
    ),
    RetailCalendar(
        event_name="Prime Day (Amazon)",
        month=7,
        day=15,
        typical_order_window_weeks_before=6,
        container_demand_multiplier=1.25,
        affected_routes=["transpacific_eb"],
        description=(
            "Amazon Prime Day (typically mid-July) drives FBA sellers to book trans-Pacific "
            "containers in May-June to replenish US warehouse stock before the sale event. "
            "Spot rates on Asia-US West Coast often jump 15-25% in this booking window."
        ),
    ),
]


# ── Monthly demand index by route ─────────────────────────────────────────────

# Base demand index by month for trans-Pacific EB (1.0 = average)
# Derived from historical SCFI/CCFI seasonality + e-commerce retail calendar
_TRANSPACIFIC_EB_MONTHLY_INDEX: dict[int, float] = {
    1:  0.85,   # January:   post-holiday softness, pre-CNY rush fading
    2:  0.70,   # February:  CNY factory closure — significant dip
    3:  0.80,   # March:     recovery, Easter booking starts
    4:  0.90,   # April:     spring build, Easter deliveries
    5:  1.10,   # May:       Prime Day booking surge begins, summer build starts
    6:  1.25,   # June:      Peak booking: Prime Day + summer inventory
    7:  1.40,   # July:      Peak season — summer inventory + holiday pre-booking
    8:  1.45,   # August:    True peak — holiday containers booking window
    9:  1.35,   # September: Singles Day + Black Friday booking overlap
    10: 1.20,   # October:   Holiday goods arriving, late bookings
    11: 1.00,   # November:  Post-peak, Singles Day deliveries
    12: 0.90,   # December:  Holiday retail peak, shipping softening; pre-CNY rush
}

_ASIA_EUROPE_MONTHLY_INDEX: dict[int, float] = {
    1:  0.90,
    2:  0.75,
    3:  0.95,
    4:  1.05,
    5:  1.10,
    6:  1.15,
    7:  1.10,
    8:  1.05,
    9:  1.20,
    10: 1.15,
    11: 1.00,
    12: 0.95,
}

_INTRA_ASIA_MONTHLY_INDEX: dict[int, float] = {
    1:  0.80,
    2:  0.65,
    3:  0.85,
    4:  0.95,
    5:  1.05,
    6:  1.10,
    7:  1.15,
    8:  1.15,
    9:  1.20,
    10: 1.10,
    11: 1.05,
    12: 0.90,
}


def compute_ecommerce_demand_index(month: int) -> dict:
    """Return the current month's e-commerce demand pressure index by route.

    Args:
        month: Calendar month (1-12).

    Returns:
        Dict with route keys mapped to demand index dicts containing
        ``index``, ``label``, and ``active_signals`` count.
    """
    logger.debug("Computing e-commerce demand index for month {}", month)

    if month < 1 or month > 12:
        raise ValueError("month must be 1-12, got " + str(month))

    tp_idx   = _TRANSPACIFIC_EB_MONTHLY_INDEX[month]
    ae_idx   = _ASIA_EUROPE_MONTHLY_INDEX[month]
    ia_idx   = _INTRA_ASIA_MONTHLY_INDEX[month]

    def _label(idx: float) -> str:
        if idx >= 1.35:
            return "VERY HIGH"
        if idx >= 1.15:
            return "HIGH"
        if idx >= 0.95:
            return "MODERATE"
        if idx >= 0.80:
            return "LOW-MODERATE"
        return "LOW"

    # Count active e-commerce signals for the month
    active_tp = sum(
        1
        for sigs in ECOMMERCE_SIGNALS.values()
        for s in sigs
        if "transpacific_eb" in s.affected_routes
    )
    active_ae = sum(
        1
        for sigs in ECOMMERCE_SIGNALS.values()
        for s in sigs
        if "asia_europe" in s.affected_routes
    )
    active_ia = sum(
        1
        for sigs in ECOMMERCE_SIGNALS.values()
        for s in sigs
        if "intra_asia_sea" in s.affected_routes
    )

    return {
        "month": month,
        "transpacific_eb": {
            "index": tp_idx,
            "label": _label(tp_idx),
            "active_signals": active_tp,
        },
        "asia_europe": {
            "index": ae_idx,
            "label": _label(ae_idx),
            "active_signals": active_ae,
        },
        "intra_asia_sea": {
            "index": ia_idx,
            "label": _label(ia_idx),
            "active_signals": active_ia,
        },
    }


def get_seasonal_booking_windows() -> list[dict]:
    """Return upcoming booking windows that importers should act on now.

    Evaluates the retail calendar against today's date and returns a list
    of dicts describing each upcoming event, sorted by urgency (weeks until
    the booking window opens or closes).

    Returns:
        List of dicts with keys: event_name, event_date, book_by_date,
        weeks_until_book_by, weeks_until_event, demand_multiplier,
        affected_routes, urgency_level, description.
    """
    logger.debug("Computing seasonal booking windows from today")

    today = date.today()
    windows: list[dict] = []

    for cal in RETAIL_CALENDAR:
        # Try event in current year first, then next year if already past
        for year_offset in (0, 1):
            try:
                event_date = date(today.year + year_offset, cal.month, cal.day)
            except ValueError:
                # e.g. Feb 29 in non-leap year
                event_date = date(today.year + year_offset, cal.month, 28)

            if event_date < today:
                continue  # past — try next year

            book_by_date = event_date - timedelta(weeks=cal.typical_order_window_weeks_before)
            weeks_until_event   = max(0, (event_date - today).days // 7)
            weeks_until_book_by = (book_by_date - today).days // 7

            if weeks_until_event > 52:
                break  # beyond a year — skip

            if weeks_until_book_by <= 0:
                urgency = "CRITICAL"   # booking window already open or closing
            elif weeks_until_book_by <= 4:
                urgency = "HIGH"
            elif weeks_until_book_by <= 10:
                urgency = "MODERATE"
            else:
                urgency = "MONITOR"

            windows.append({
                "event_name":             cal.event_name,
                "event_date":             event_date,
                "book_by_date":           book_by_date,
                "weeks_until_book_by":    weeks_until_book_by,
                "weeks_until_event":      weeks_until_event,
                "demand_multiplier":      cal.container_demand_multiplier,
                "affected_routes":        cal.affected_routes,
                "urgency_level":          urgency,
                "description":            cal.description,
            })
            break  # found a valid future occurrence — no need to try next year

    # Sort: CRITICAL first, then by weeks_until_book_by ascending
    urgency_order = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "MONITOR": 3}
    windows.sort(key=lambda w: (urgency_order.get(w["urgency_level"], 4), w["weeks_until_book_by"]))

    logger.info("Returning {} upcoming booking windows", len(windows))
    return windows
