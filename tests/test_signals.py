"""Tests for engine.signals — SignalComponent + direction_from_score."""
from __future__ import annotations

import pytest

from engine.signals import SignalComponent, direction_from_score


# ─── SignalComponent dataclass ─────────────────────────────────────────────

def test_signal_component_shape() -> None:
    sc = SignalComponent(
        name="BDI", value=0.8, weight=0.3,
        label="Above 90d avg", direction="bullish",
    )
    assert sc.name == "BDI"
    assert sc.value == 0.8
    assert sc.weight == 0.3
    assert sc.label == "Above 90d avg"
    assert sc.direction == "bullish"


def test_signal_component_contribution_is_value_times_weight() -> None:
    sc = SignalComponent(
        name="x", value=0.6, weight=0.5,
        label="", direction="neutral",
    )
    assert sc.contribution == pytest.approx(0.30)


def test_signal_component_contribution_at_boundaries() -> None:
    # Zero value
    sc = SignalComponent("x", 0.0, 0.5, "", "neutral")
    assert sc.contribution == 0.0
    # Zero weight
    sc = SignalComponent("x", 0.8, 0.0, "", "neutral")
    assert sc.contribution == 0.0
    # Unit value + weight
    sc = SignalComponent("x", 1.0, 1.0, "", "neutral")
    assert sc.contribution == 1.0


def test_signal_component_direction_emoji() -> None:
    bullish = SignalComponent("x", 1.0, 1.0, "", "bullish")
    bearish = SignalComponent("x", 1.0, 1.0, "", "bearish")
    neutral = SignalComponent("x", 1.0, 1.0, "", "neutral")
    assert bullish.direction_emoji == "↑"
    assert bearish.direction_emoji == "↓"
    assert neutral.direction_emoji == "→"


def test_signal_component_unknown_direction_falls_back_to_neutral_arrow() -> None:
    sc = SignalComponent("x", 1.0, 1.0, "", "weird")
    assert sc.direction_emoji == "→"


# ─── direction_from_score ──────────────────────────────────────────────────

def test_direction_from_score_default_thresholds() -> None:
    # Default: >=0.60 bullish, <=0.40 bearish, else neutral
    assert direction_from_score(0.80) == "bullish"
    assert direction_from_score(0.60) == "bullish"      # boundary inclusive
    assert direction_from_score(0.50) == "neutral"
    assert direction_from_score(0.40) == "bearish"      # boundary inclusive
    assert direction_from_score(0.20) == "bearish"


def test_direction_from_score_at_exact_midpoint() -> None:
    # 0.50 is between 0.40 and 0.60 → neutral
    assert direction_from_score(0.50) == "neutral"


def test_direction_from_score_custom_thresholds() -> None:
    # Tighter bullish threshold
    assert direction_from_score(0.65, high=0.70, low=0.30) == "neutral"
    assert direction_from_score(0.71, high=0.70, low=0.30) == "bullish"
    # Tighter bearish threshold
    assert direction_from_score(0.25, high=0.70, low=0.30) == "bearish"
    assert direction_from_score(0.35, high=0.70, low=0.30) == "neutral"


def test_direction_from_score_extreme_values() -> None:
    assert direction_from_score(0.0) == "bearish"
    assert direction_from_score(1.0) == "bullish"
    assert direction_from_score(-0.5) == "bearish"     # below 0 still bearish
    assert direction_from_score(1.5) == "bullish"      # above 1 still bullish


def test_direction_from_score_with_inverted_thresholds() -> None:
    """If high < low, neither branch matches; falls through to neutral."""
    assert direction_from_score(0.5, high=0.3, low=0.7) == "bullish"  # 0.5 >= 0.3
