from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SignalComponent:
    """A single weighted signal contributing to an insight score."""
    name: str                # Human-readable name (e.g. "Baltic Dry Index")
    value: float             # Normalized value [0, 1]
    weight: float            # Weight in parent score sum
    label: str               # Human-readable interpretation (e.g. "Above 90d average")
    direction: str           # "bullish" | "bearish" | "neutral"

    @property
    def contribution(self) -> float:
        """Weighted contribution to parent score."""
        return self.value * self.weight

    @property
    def direction_emoji(self) -> str:
        return {"bullish": "↑", "bearish": "↓", "neutral": "→"}.get(self.direction, "→")


def direction_from_score(score: float, high: float = 0.60, low: float = 0.40) -> str:
    """Classify a [0,1] score as bullish/bearish/neutral."""
    if score >= high:
        return "bullish"
    if score <= low:
        return "bearish"
    return "neutral"
