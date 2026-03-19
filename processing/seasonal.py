"""
Seasonal pattern analysis for container shipping.

Key known seasonal patterns:
- Pre-Chinese New Year (Jan/Feb): Asia exports SURGE in Dec-Jan before factory shutdowns
- Chinese New Year (Feb): Asia exports DROP ~30-40% for 2-3 weeks
- Peak Season (Aug-Oct): Trans-Pacific EB rates spike as retailers stock for holiday season
- Post-Lunar New Year Recovery (Mar-Apr): Gradual rebuild
- Slow Season (Nov-Jan excl. CNY prep): Rate softness
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class SeasonalSignal:
    name: str
    description: str
    direction: str          # "bullish" | "bearish" | "neutral"
    strength: float         # [0, 1] — how strong is the seasonal effect
    affected_routes: list[str]
    affected_regions: list[str]
    days_until: int         # days until next occurrence (negative = active now)
    active_now: bool


# Seasonal calendar: (month_start, day_start, month_end, day_end, name, direction, strength, routes, regions)
_SEASONAL_EVENTS = [
    # Chinese New Year prep surge (varies ~Jan 20 - Feb 10 each year; use Jan 15 - Feb 5 as window)
    {
        "name": "Pre-CNY Export Surge",
        "month_start": 12, "day_start": 1,
        "month_end": 1, "day_end": 25,
        "description": "Chinese factories rush exports before shutting down for Chinese New Year. Trans-Pacific and Asia-Europe rates typically rise 10-25%.",
        "direction": "bullish",
        "strength": 0.80,
        "affected_routes": ["transpacific_eb", "asia_europe"],
        "affected_regions": ["Asia East", "North America West", "Europe"],
    },
    {
        "name": "Chinese New Year Slowdown",
        "month_start": 1, "day_start": 25,
        "month_end": 2, "day_end": 28,
        "description": "Chinese factories shut for 2-4 weeks. Asia exports drop significantly. Container availability tightens as ships wait.",
        "direction": "bearish",
        "strength": 0.75,
        "affected_routes": ["transpacific_eb", "asia_europe", "intra_asia_sea"],
        "affected_regions": ["Asia East"],
    },
    {
        "name": "Post-CNY Recovery",
        "month_start": 3, "day_start": 1,
        "month_end": 4, "day_end": 30,
        "description": "Chinese factories restart. Demand rebuilds gradually. Rates often dip then recover.",
        "direction": "neutral",
        "strength": 0.40,
        "affected_routes": ["transpacific_eb", "asia_europe"],
        "affected_regions": ["Asia East"],
    },
    {
        "name": "Peak Season Build",
        "month_start": 7, "day_start": 1,
        "month_end": 9, "day_end": 30,
        "description": "Retailers stock for holiday season. Trans-Pacific EB rates typically at annual high. Container shortages common.",
        "direction": "bullish",
        "strength": 0.85,
        "affected_routes": ["transpacific_eb", "transpacific_wb"],
        "affected_regions": ["Asia East", "North America West"],
    },
    {
        "name": "Holiday Peak",
        "month_start": 10, "day_start": 1,
        "month_end": 11, "day_end": 15,
        "description": "Pre-holiday inventory stocking at maximum. Ports congested. Rates elevated across all major lanes.",
        "direction": "bullish",
        "strength": 0.70,
        "affected_routes": ["transpacific_eb", "asia_europe", "transatlantic"],
        "affected_regions": ["North America West", "North America East", "Europe"],
    },
    {
        "name": "Post-Holiday Lull",
        "month_start": 11, "day_start": 15,
        "month_end": 12, "day_end": 15,
        "description": "Retailers have stocked. Import demand falls. Rate softness common.",
        "direction": "bearish",
        "strength": 0.50,
        "affected_routes": ["transpacific_eb", "transatlantic"],
        "affected_regions": ["North America West", "North America East"],
    },
    {
        "name": "Ramadan Effect",
        "month_start": 3, "day_start": 1,
        "month_end": 4, "day_end": 15,
        "description": "Middle East trade slows during Ramadan. Jebel Ali throughput dips. Recovers sharply post-Eid.",
        "direction": "bearish",
        "strength": 0.35,
        "affected_routes": ["middle_east_europe", "middle_east_asia"],
        "affected_regions": ["Middle East"],
    },
]


def get_active_seasonal_signals(reference_date: date | None = None) -> list[SeasonalSignal]:
    """Return all seasonal signals, both currently active and upcoming within 60 days."""
    if reference_date is None:
        reference_date = date.today()

    results: list[SeasonalSignal] = []

    for event in _SEASONAL_EVENTS:
        signal = _evaluate_event(event, reference_date)
        results.append(signal)

    # Sort: active first, then by days_until ascending
    results.sort(key=lambda s: (not s.active_now, s.days_until))
    return results


def get_seasonal_adjustment(route_id: str, reference_date: date | None = None) -> float:
    """Return a seasonal score adjustment for a route, range [-0.15, +0.15].

    Positive = seasonal tailwind, negative = seasonal headwind.
    Used to fine-tune route opportunity scores.
    """
    if reference_date is None:
        reference_date = date.today()

    adjustment = 0.0
    for event in _SEASONAL_EVENTS:
        signal = _evaluate_event(event, reference_date)
        if signal.active_now and route_id in signal.affected_routes:
            if signal.direction == "bullish":
                adjustment += signal.strength * 0.15
            elif signal.direction == "bearish":
                adjustment -= signal.strength * 0.15

    return max(-0.15, min(0.15, adjustment))


def _evaluate_event(event: dict, ref: date) -> SeasonalSignal:
    """Determine if a seasonal event is active on ref date and compute days_until."""
    m_start, d_start = event["month_start"], event["day_start"]
    m_end, d_end = event["month_end"], event["day_end"]
    year = ref.year

    # Handle year-wrap (e.g. Dec 1 → Jan 25)
    try:
        start = date(year, m_start, d_start)
        end = date(year, m_end, d_end)
        if end < start:  # wraps into next year
            end = date(year + 1, m_end, d_end)
    except ValueError:
        # Handle Feb 29 edge case
        start = date(year, m_start, min(d_start, 28))
        end = date(year, m_end, min(d_end, 28))

    active_now = start <= ref <= end

    if active_now:
        days_until = -(ref - start).days
    else:
        # Find next occurrence
        if ref < start:
            days_until = (start - ref).days
        else:
            # Already past this year's window; next year
            try:
                next_start = date(year + 1, m_start, d_start)
            except ValueError:
                next_start = date(year + 1, m_start, min(d_start, 28))
            days_until = (next_start - ref).days

    return SeasonalSignal(
        name=event["name"],
        description=event["description"],
        direction=event["direction"],
        strength=event["strength"],
        affected_routes=event["affected_routes"],
        affected_regions=event["affected_regions"],
        days_until=days_until,
        active_now=active_now,
    )
