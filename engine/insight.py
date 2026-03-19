from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from engine.signals import SignalComponent

if TYPE_CHECKING:
    pass


INSIGHT_CATEGORIES = ["PORT_DEMAND", "ROUTE", "MACRO", "CONVERGENCE"]
INSIGHT_ACTIONS = {
    "strong_bullish":  "Prioritize",
    "mild_bullish":    "Monitor",
    "neutral":         "Watch",
    "mild_bearish":    "Caution",
    "strong_bearish":  "Avoid",
}


@dataclass
class Insight:
    """An actionable insight produced by the decision engine.

    The primary output object — one per surfaced opportunity or risk.
    Sorted by score descending in the results tab.
    """
    insight_id: str
    title: str                                # Short action-oriented title
    category: str                             # PORT_DEMAND | ROUTE | MACRO | CONVERGENCE
    score: float                              # [0, 1] composite confidence
    score_label: str                          # "Strong" | "Moderate" | "Weak"
    action: str                               # Verb phrase: "Prioritize", "Monitor", etc.
    detail: str                               # 2-3 sentence explanation

    supporting_signals: list[SignalComponent] # Each sub-signal with value + weight
    ports_involved: list[str]                 # LOCODEs
    routes_involved: list[str]                # route_ids
    stocks_potentially_affected: list[str]    # Ticker symbols

    generated_at: str
    data_freshness_warning: bool = False      # True if any input > 2x its TTL


def make_insight(
    title: str,
    category: str,
    score: float,
    detail: str,
    signals: list[SignalComponent],
    ports: list[str] | None = None,
    routes: list[str] | None = None,
    stocks: list[str] | None = None,
    freshness_warning: bool = False,
) -> Insight:
    """Factory function for creating Insight objects."""
    from utils.helpers import score_to_label, now_iso

    # Determine action verb based on score and category
    if score >= 0.75:
        action = "Prioritize"
    elif score >= 0.60:
        action = "Monitor"
    elif score >= 0.45:
        action = "Watch"
    elif score >= 0.30:
        action = "Caution"
    else:
        action = "Avoid"

    return Insight(
        insight_id=str(uuid.uuid4())[:8],
        title=title,
        category=category,
        score=score,
        score_label=score_to_label(score),
        action=action,
        detail=detail,
        supporting_signals=signals,
        ports_involved=ports or [],
        routes_involved=routes or [],
        stocks_potentially_affected=stocks or [],
        generated_at=now_iso(),
        data_freshness_warning=freshness_warning,
    )
