"""
Shipping events calendar database.

Covers major holidays, seasonal cycles, regulatory events, and trade windows
that materially affect container shipping rates and capacity utilisation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal


@dataclass
class ShippingEvent:
    name: str
    event_type: str           # "holiday" | "season" | "regulatory" | "trade" | "market"
    start_date: date
    end_date: date
    impact: str               # "BULLISH" | "BEARISH" | "MIXED" | "NEUTRAL"
    impact_magnitude: float   # 0-1
    affected_routes: list[str]   # list of route_ids or ["ALL"]
    description: str
    action_recommendation: str


# ---------------------------------------------------------------------------
# Master events list
# ---------------------------------------------------------------------------

SHIPPING_EVENTS_2025_2026: list[ShippingEvent] = [

    # ── 2025 Pre-CNY rush ────────────────────────────────────────────────────
    ShippingEvent(
        name="Pre-CNY Inventory Rush 2025",
        event_type="season",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 28),
        impact="BULLISH",
        impact_magnitude=0.82,
        affected_routes=["transpacific_eb", "asia_europe", "intra_asia_sea"],
        description=(
            "Chinese factories accelerate shipments ahead of the Lunar New Year shutdown. "
            "Spot rates on Asia-origin lanes surge 10-30% as exporters race to clear orders. "
            "Container availability tightens at major Chinese ports."
        ),
        action_recommendation=(
            "Secure capacity early; spot rates will peak in the final week. "
            "Consider booking 2-3 weeks ahead of normal lead times."
        ),
    ),

    # ── 2025 Chinese New Year ────────────────────────────────────────────────
    ShippingEvent(
        name="Chinese New Year 2025",
        event_type="holiday",
        start_date=date(2025, 1, 29),
        end_date=date(2025, 2, 12),
        impact="BEARISH",
        impact_magnitude=0.75,
        affected_routes=["transpacific_eb", "asia_europe", "intra_asia_sea"],
        description=(
            "Chinese factories shut for 2+ weeks. Asia export volumes drop 30-40%. "
            "Ships accumulate at anchor; berth productivity falls. Rate softness follows "
            "the initial supply glut as vessels return to market simultaneously."
        ),
        action_recommendation=(
            "Avoid booking Asia-origin spot capacity during the shutdown window — "
            "rates spike briefly then collapse. Target contract resets post-holiday."
        ),
    ),

    # ── Valentine's Day import peak ──────────────────────────────────────────
    ShippingEvent(
        name="Valentine's Day Imports Peak",
        event_type="trade",
        start_date=date(2024, 12, 1),
        end_date=date(2025, 2, 14),
        impact="BULLISH",
        impact_magnitude=0.40,
        affected_routes=["transpacific_eb"],
        description=(
            "Seasonal demand for flowers, chocolates, gifts, and apparel drives an uplift "
            "in Trans-Pacific eastbound volumes from November through mid-February. "
            "Less dominant than peak season but adds incremental rate pressure."
        ),
        action_recommendation=(
            "Mild demand tailwind on Trans-Pacific EB. Factor into capacity planning "
            "for holiday goods importers but not a market-moving event on its own."
        ),
    ),

    # ── 2025 Spring restocking ───────────────────────────────────────────────
    ShippingEvent(
        name="Spring Restocking 2025",
        event_type="season",
        start_date=date(2025, 3, 15),
        end_date=date(2025, 4, 30),
        impact="BULLISH",
        impact_magnitude=0.55,
        affected_routes=["transpacific_eb", "asia_europe"],
        description=(
            "Post-CNY factory restart drives a spring restocking wave as US and European "
            "retailers replenish depleted inventories. Rate recovery typically follows "
            "the CNY trough by 4-6 weeks."
        ),
        action_recommendation=(
            "Good window to lock in medium-term contracts before peak-season premiums "
            "kick in. Rates should be near-seasonal lows through mid-March."
        ),
    ),

    # ── Ramadan 2025 ─────────────────────────────────────────────────────────
    ShippingEvent(
        name="Ramadan 2025",
        event_type="holiday",
        start_date=date(2025, 3, 1),
        end_date=date(2025, 3, 30),
        impact="MIXED",
        impact_magnitude=0.35,
        affected_routes=["middle_east_europe", "middle_east_asia"],
        description=(
            "Trade flows through Middle East ports (Jebel Ali, King Abdullah Port) dip "
            "during Ramadan due to reduced working hours and productivity. Consumer goods "
            "imports into the Gulf decline temporarily. Recovery is sharp post-Eid."
        ),
        action_recommendation=(
            "Build 3-5 day buffer into Middle East delivery windows. "
            "Pre-position stock before Ramadan or plan for post-Eid replenishment surge."
        ),
    ),

    # ── US Memorial Day ───────────────────────────────────────────────────────
    ShippingEvent(
        name="US Memorial Day 2025",
        event_type="holiday",
        start_date=date(2025, 5, 26),
        end_date=date(2025, 5, 26),
        impact="NEUTRAL",
        impact_magnitude=0.15,
        affected_routes=["transpacific_eb", "transatlantic"],
        description=(
            "US federal holiday; port operations continue but trucking, customs, and "
            "warehouse drayage slow for 1-2 days. Minor disruption to inland container "
            "movements on the US east and west coasts."
        ),
        action_recommendation=(
            "Allow a 1-2 day buffer for US inland deliveries scheduled around Memorial Day. "
            "No material impact on ocean freight rates."
        ),
    ),

    # ── 2025 Back-to-school imports ───────────────────────────────────────────
    ShippingEvent(
        name="Back-to-School Imports 2025",
        event_type="trade",
        start_date=date(2025, 6, 15),
        end_date=date(2025, 8, 15),
        impact="BULLISH",
        impact_magnitude=0.60,
        affected_routes=["transpacific_eb"],
        description=(
            "Seasonal surge in school supplies, clothing, electronics, and sporting goods "
            "imported from Asia for the US back-to-school retail window. Overlaps with "
            "peak season build, amplifying Trans-Pacific EB rate pressure."
        ),
        action_recommendation=(
            "Capacity on Trans-Pacific EB will be tight June-August. "
            "Book 6-8 weeks ahead; expect GRIs and peak season surcharges."
        ),
    ),

    # ── 2025 Peak Season ─────────────────────────────────────────────────────
    ShippingEvent(
        name="Peak Season 2025",
        event_type="season",
        start_date=date(2025, 7, 1),
        end_date=date(2025, 9, 15),
        impact="BULLISH",
        impact_magnitude=0.87,
        affected_routes=["ALL"],
        description=(
            "Annual summer peak: retailers across North America and Europe stock for the "
            "holiday season. Trans-Pacific EB rates typically reach annual highs. "
            "Container shortages common; vessel utilisation >95% on major lanes. "
            "Port congestion at LA/LB, Rotterdam, and Hamburg frequently reported."
        ),
        action_recommendation=(
            "Prioritise long-term contract coverage. If spot-dependent, book immediately "
            "and expect premium surcharges. Peak season surcharges often add $1,000-2,500/FEU."
        ),
    ),

    # ── US Labor Day 2025 ─────────────────────────────────────────────────────
    ShippingEvent(
        name="US Labor Day 2025",
        event_type="holiday",
        start_date=date(2025, 9, 1),
        end_date=date(2025, 9, 1),
        impact="NEUTRAL",
        impact_magnitude=0.12,
        affected_routes=["transpacific_eb", "transatlantic"],
        description=(
            "US federal holiday. Similar to Memorial Day — port operations largely "
            "continue but ancillary logistics (drayage, warehousing) slow for one day."
        ),
        action_recommendation=(
            "Add a 1-day inland delivery buffer around Labor Day. "
            "Marks the informal end of US summer and beginning of holiday inventory push."
        ),
    ),

    # ── 2025 Pre-Christmas rush ───────────────────────────────────────────────
    ShippingEvent(
        name="Pre-Christmas Rush 2025",
        event_type="season",
        start_date=date(2025, 10, 1),
        end_date=date(2025, 11, 15),
        impact="BULLISH",
        impact_magnitude=0.72,
        affected_routes=["transpacific_eb"],
        description=(
            "The final wave of holiday inventory arrives on Trans-Pacific EB lanes. "
            "Retailers prioritise speed over cost to ensure Christmas stock availability. "
            "Spot rates remain elevated; premium services (expedited, assured) heavily used."
        ),
        action_recommendation=(
            "Spot rates stay elevated into November. Avoid last-minute bookings — "
            "rollovers are common as carriers protect contract customers."
        ),
    ),

    # ── Thanksgiving 2025 ────────────────────────────────────────────────────
    ShippingEvent(
        name="Thanksgiving 2025",
        event_type="holiday",
        start_date=date(2025, 11, 27),
        end_date=date(2025, 11, 27),
        impact="BEARISH",
        impact_magnitude=0.20,
        affected_routes=["transpacific_eb", "transatlantic"],
        description=(
            "US holiday; inland logistics effectively halt for 3-4 days (Wed-Sun). "
            "Port gate moves drop sharply. Short-term bearish for US inland container "
            "velocity, though ocean bookings are unaffected."
        ),
        action_recommendation=(
            "Do not schedule time-sensitive inland US deliveries the week of Thanksgiving. "
            "Plan for drayage delays of 3-5 business days around this window."
        ),
    ),

    # ── Holiday peak 2025 ────────────────────────────────────────────────────
    ShippingEvent(
        name="Holiday Peak 2025",
        event_type="season",
        start_date=date(2025, 11, 15),
        end_date=date(2025, 12, 15),
        impact="BULLISH",
        impact_magnitude=0.65,
        affected_routes=["ALL"],
        description=(
            "Peak retail season: Black Friday, Cyber Monday, and Christmas demand drives "
            "elevated container volumes across all major trade lanes. Ports congested; "
            "chassis and warehouse space at premium across US and European hubs."
        ),
        action_recommendation=(
            "Expect rate premiums across all lanes. Book capacity and chassis early; "
            "use premium guaranteed services for time-critical shipments."
        ),
    ),

    # ── Year-end slowdown 2025/26 ─────────────────────────────────────────────
    ShippingEvent(
        name="Year-End Slowdown 2025-2026",
        event_type="season",
        start_date=date(2025, 12, 20),
        end_date=date(2026, 1, 6),
        impact="BEARISH",
        impact_magnitude=0.55,
        affected_routes=["ALL"],
        description=(
            "Global holiday period: factory output slows across Asia, Europe, and the "
            "Americas. Ocean booking volumes drop sharply. Rates typically soften 10-20% "
            "before the pre-CNY 2026 surge kicks in."
        ),
        action_recommendation=(
            "Good window for contract negotiations and rate resets. "
            "Avoid urgent spot bookings — capacity available but carrier reliability dips."
        ),
    ),

    # ── 2026 Pre-CNY rush ────────────────────────────────────────────────────
    ShippingEvent(
        name="Pre-CNY Rush 2026",
        event_type="season",
        start_date=date(2026, 1, 5),
        end_date=date(2026, 2, 16),
        impact="BULLISH",
        impact_magnitude=0.80,
        affected_routes=["transpacific_eb", "asia_europe", "intra_asia_sea"],
        description=(
            "Export surge from Chinese factories ahead of the 2026 Lunar New Year shutdown "
            "(Feb 17). Spot rates on Asia-origin lanes rally strongly through late January "
            "and the first two weeks of February."
        ),
        action_recommendation=(
            "Lock in capacity by early January for February shipments. "
            "Expect GRIs of $500-1,500/FEU; negotiate FAK rates before the rush."
        ),
    ),

    # ── 2026 Chinese New Year ────────────────────────────────────────────────
    ShippingEvent(
        name="Chinese New Year 2026",
        event_type="holiday",
        start_date=date(2026, 2, 17),
        end_date=date(2026, 3, 3),
        impact="BEARISH",
        impact_magnitude=0.75,
        affected_routes=["transpacific_eb", "asia_europe", "intra_asia_sea"],
        description=(
            "2026 Lunar New Year (Year of the Horse). Chinese factory shutdowns last "
            "2-3 weeks. Export volumes fall 30-40% from Asian ports. "
            "Similar dynamics to CNY 2025: brief rate spike pre-holiday, then sharp collapse."
        ),
        action_recommendation=(
            "Shift shipments to pre-CNY window (Jan 5 - Feb 16) or post-holiday recovery "
            "(mid-March onward). Avoid spot bookings during the shutdown."
        ),
    ),

    # ── Q1 2026 inventory restocking ─────────────────────────────────────────
    ShippingEvent(
        name="Q1 2026 Inventory Restocking",
        event_type="season",
        start_date=date(2026, 1, 15),
        end_date=date(2026, 3, 31),
        impact="BULLISH",
        impact_magnitude=0.58,
        affected_routes=["transpacific_eb"],
        description=(
            "US retailers rebuild inventory following the holiday season sell-through and "
            "supply chain disruptions of Q4 2025. Demand for consumer goods, electronics, "
            "and household items drives Trans-Pacific EB volume recovery."
        ),
        action_recommendation=(
            "Trans-Pacific EB demand strengthens through Q1. "
            "Monitor inventory-to-sales ratios for forward booking signals."
        ),
    ),

    # ── Summer 2026 peak season ───────────────────────────────────────────────
    ShippingEvent(
        name="Peak Season 2026",
        event_type="season",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 9, 15),
        impact="BULLISH",
        impact_magnitude=0.87,
        affected_routes=["ALL"],
        description=(
            "2026 annual summer peak season. Historically the strongest rate period of the "
            "year. Expect high vessel utilisation, equipment shortages, and port congestion "
            "on all major trade lanes as retailers stock for the holiday season."
        ),
        action_recommendation=(
            "Begin securing peak-season capacity in Q1 2026. "
            "Long-term contracts strongly preferred over spot exposure during this window."
        ),
    ),

    # ── IMO 2025 regulations ──────────────────────────────────────────────────
    ShippingEvent(
        name="IMO 2025 Fuel & Emissions Regulations",
        event_type="regulatory",
        start_date=date(2025, 1, 1),
        end_date=date(2026, 12, 31),
        impact="NEUTRAL",
        impact_magnitude=0.30,
        affected_routes=["ALL"],
        description=(
            "Ongoing IMO Carbon Intensity Indicator (CII) ratings and FuelEU Maritime "
            "requirements affect vessel speed, fuel choice, and operational cost. "
            "Slow steaming may marginally tighten effective capacity on long-haul lanes."
        ),
        action_recommendation=(
            "Factor a 2-4% effective capacity reduction into planning assumptions. "
            "Bunker surcharge volatility elevated; hedge fuel exposure where possible."
        ),
    ),

    # ── Suez transit fee increase (hypothetical) ─────────────────────────────
    ShippingEvent(
        name="Suez Canal Transit Fee Increase",
        event_type="regulatory",
        start_date=date(2025, 4, 1),
        end_date=date(2026, 12, 31),
        impact="BEARISH",
        impact_magnitude=0.38,
        affected_routes=["asia_europe", "middle_east_europe"],
        description=(
            "Suez Canal Authority fee increases (hypothetical scenario) add $200-400/FEU "
            "to Asia-Europe base rates. Combined with Red Sea rerouting surcharges, "
            "total cost on Asia-Europe lanes remains significantly above pre-2024 levels."
        ),
        action_recommendation=(
            "Review Asia-Europe contract structures; negotiate bunker and canal surcharge "
            "caps. Consider Cape of Good Hope routings for time-insensitive cargo."
        ),
    ),

    # ── US port labor agreement ───────────────────────────────────────────────
    ShippingEvent(
        name="US East Coast Port Labor Agreement",
        event_type="regulatory",
        start_date=date(2025, 1, 1),
        end_date=date(2026, 12, 31),
        impact="NEUTRAL",
        impact_magnitude=0.20,
        affected_routes=["transatlantic", "transpacific_eb"],
        description=(
            "ILA-USMX master contract (hypothetical renewal) provides labor stability at "
            "US East and Gulf Coast ports through end of 2026. Reduces strike risk premium "
            "on USEC-bound shipments but wage increases pass through as terminal handling charges."
        ),
        action_recommendation=(
            "Reduced but not eliminated labor disruption risk. Maintain buffer stock for "
            "USEC-served distribution centres. Monitor ILA-terminal operator local negotiations."
        ),
    ),

    # ── Red Sea / Gulf of Aden disruption (ongoing) ──────────────────────────
    ShippingEvent(
        name="Red Sea / Gulf of Aden Disruption",
        event_type="market",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        impact="BEARISH",
        impact_magnitude=0.70,
        affected_routes=["asia_europe", "middle_east_europe", "middle_east_asia"],
        description=(
            "Ongoing security situation in the Red Sea forces most major carriers to reroute "
            "via the Cape of Good Hope, adding 10-14 days and $400-800/FEU in additional "
            "costs. Effective capacity on Asia-Europe routes reduced ~15-20%."
        ),
        action_recommendation=(
            "Plan for extended transit times on Asia-Europe lanes. "
            "Use Cape routing as baseline; any Suez resumption is upside surprise. "
            "War risk surcharges apply — check carrier advisories weekly."
        ),
    ),

    # ── Asia-US tariff uncertainty (hypothetical) ─────────────────────────────
    ShippingEvent(
        name="US Tariff Policy Uncertainty",
        event_type="market",
        start_date=date(2025, 2, 1),
        end_date=date(2026, 6, 30),
        impact="MIXED",
        impact_magnitude=0.50,
        affected_routes=["transpacific_eb"],
        description=(
            "Ongoing US trade policy reviews create front-loading and pull-forward demand "
            "spikes on Trans-Pacific EB when tariff increases are signalled, followed by "
            "sharp demand collapses when importers pause orders. High booking volatility."
        ),
        action_recommendation=(
            "Monitor US trade policy announcements closely — lead times for demand "
            "pull-forward can be as short as 2-4 weeks. Maintain flexible capacity options."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------

def get_upcoming_events(days_ahead: int = 60) -> list[ShippingEvent]:
    """Return events starting within the next days_ahead days, sorted by start_date."""
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    return sorted(
        [e for e in SHIPPING_EVENTS_2025_2026 if today <= e.start_date <= cutoff],
        key=lambda e: e.start_date,
    )


def get_active_events() -> list[ShippingEvent]:
    """Return events where today falls between start_date and end_date (inclusive)."""
    today = date.today()
    return [e for e in SHIPPING_EVENTS_2025_2026 if e.start_date <= today <= e.end_date]


def get_events_for_route(route_id: str, days_ahead: int = 90) -> list[ShippingEvent]:
    """Return upcoming events that affect a specific route."""
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    result = []
    for e in SHIPPING_EVENTS_2025_2026:
        if e.start_date > cutoff:
            continue
        if e.end_date < today:
            continue
        if "ALL" in e.affected_routes or route_id in e.affected_routes:
            result.append(e)
    result.sort(key=lambda e: e.start_date)
    return result


def get_days_until(event: ShippingEvent) -> int:
    """Return days until event start. Negative if already started."""
    return (event.start_date - date.today()).days


def get_market_calendar_summary() -> dict:
    """Return a high-level calendar summary dict."""
    today = date.today()
    active = get_active_events()
    upcoming = get_upcoming_events(days_ahead=60)

    # Find the next major event (BULLISH or BEARISH, magnitude > 0.5) that hasn't started
    next_major: ShippingEvent | None = None
    days_to_next = 0
    candidates = [
        e for e in SHIPPING_EVENTS_2025_2026
        if e.start_date > today
        and e.impact in ("BULLISH", "BEARISH")
        and e.impact_magnitude > 0.5
    ]
    candidates.sort(key=lambda e: e.start_date)
    if candidates:
        next_major = candidates[0]
        days_to_next = (next_major.start_date - today).days

    # Compute a simple market bias from active events
    bias_score = 0.0
    for e in active:
        if e.impact == "BULLISH":
            bias_score += e.impact_magnitude
        elif e.impact == "BEARISH":
            bias_score -= e.impact_magnitude

    if bias_score > 0.3:
        current_bias = "BULLISH"
    elif bias_score < -0.3:
        current_bias = "BEARISH"
    else:
        current_bias = "NEUTRAL"

    return {
        "active_events": active,
        "upcoming_events": upcoming,
        "next_major_event": next_major,
        "days_to_next_event": days_to_next,
        "current_market_bias": current_bias,
    }
