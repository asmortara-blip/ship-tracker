"""Tests for processing.inventory_analyzer — inventory-cycle phase classifier.

Covers:
  - InventoryCycleSignal dataclass shape
  - analyze_inventory_cycle:
      * returns None when ISRATIO missing / has < 12 obs
      * computes 5yr vs current, trend_pct_6m, trend_direction labels
      * optional consumer sentiment + new orders direction
  - _classify_phase: the 6 branches (RESTOCK falling, RESTOCK lean,
    DRAWDOWN rising, DRAWDOWN flat, BUILDING normalizing, NEUTRAL default)
  - get_inventory_score_for_engine: 0.5 fallback when None signal,
    else propagates signal.score
"""
from __future__ import annotations

import pandas as pd
import pytest

from processing.inventory_analyzer import (
    InventoryCycleSignal,
    _LEAN_THRESHOLD,
    _OVERSTOCK_THRESHOLD,
    _classify_phase,
    analyze_inventory_cycle,
    get_inventory_score_for_engine,
)


def _is_df(values: list[float]) -> pd.DataFrame:
    """Build a monthly FRED-shaped ISRATIO frame."""
    return pd.DataFrame({
        "date": pd.date_range("2020-01-01", periods=len(values), freq="MS"),
        "value": values,
    })


# ─── InventoryCycleSignal dataclass ─────────────────────────────────────────

def test_inventory_cycle_signal_shape() -> None:
    sig = InventoryCycleSignal(
        phase="RESTOCK", phase_description="d",
        is_ratio_current=1.30, is_ratio_retail=1.25,
        is_ratio_vs_5yr_avg=-0.05, trend_direction="Falling",
        trend_pct_6m=-0.02, shipping_implication="impl",
        score=0.80, consumer_sentiment=82.0, new_orders_trend="Rising",
    )
    assert sig.phase == "RESTOCK"
    assert sig.score == 0.80


# ─── analyze_inventory_cycle: input-guard branches ─────────────────────────

def test_analyze_inventory_cycle_returns_none_when_no_isratio_key() -> None:
    assert analyze_inventory_cycle({}) is None


def test_analyze_inventory_cycle_returns_none_when_isratio_too_short() -> None:
    # Needs >= 12 observations
    assert analyze_inventory_cycle({"ISRATIO": _is_df([1.40] * 11)}) is None


def test_analyze_inventory_cycle_returns_none_when_isratio_empty() -> None:
    assert analyze_inventory_cycle({"ISRATIO": pd.DataFrame()}) is None


# ─── analyze_inventory_cycle: happy paths ───────────────────────────────────

def test_analyze_inventory_cycle_lean_falling_produces_restock_phase() -> None:
    """I:S ratio drops from 1.45 to 1.28 over the trailing window → RESTOCK."""
    # 14 obs, ending lean and falling
    series = [1.45, 1.44, 1.42, 1.40, 1.40, 1.38, 1.36, 1.34, 1.34, 1.32, 1.30, 1.30, 1.29, 1.28]
    sig = analyze_inventory_cycle({"ISRATIO": _is_df(series)})
    assert sig is not None
    assert sig.phase == "RESTOCK"
    assert sig.is_ratio_current == pytest.approx(1.28)
    assert sig.trend_direction == "Falling"


def test_analyze_inventory_cycle_overstocked_rising_produces_drawdown() -> None:
    series = [1.45, 1.46, 1.47, 1.48, 1.49, 1.50, 1.51, 1.52, 1.53, 1.54, 1.55, 1.56, 1.57, 1.58]
    sig = analyze_inventory_cycle({"ISRATIO": _is_df(series)})
    assert sig is not None
    assert sig.phase == "DRAWDOWN"
    assert sig.trend_direction == "Rising"


def test_analyze_inventory_cycle_stable_normal_produces_neutral() -> None:
    """Flat at 1.42 (normal) → NEUTRAL phase."""
    sig = analyze_inventory_cycle({"ISRATIO": _is_df([1.42] * 14)})
    assert sig is not None
    assert sig.phase == "NEUTRAL"
    assert sig.trend_direction == "Stable"


def test_analyze_inventory_cycle_pulls_retail_ratio_when_present() -> None:
    macro = {
        "ISRATIO": _is_df([1.42] * 14),
        "MRTSIR44X722USS": _is_df([1.35] * 14),
    }
    sig = analyze_inventory_cycle(macro)
    assert sig is not None
    assert sig.is_ratio_retail == pytest.approx(1.35)


def test_analyze_inventory_cycle_attaches_consumer_sentiment() -> None:
    macro = {
        "ISRATIO": _is_df([1.42] * 14),
        "UMCSENT": _is_df([78.0] * 14),
    }
    sig = analyze_inventory_cycle(macro)
    assert sig is not None
    assert sig.consumer_sentiment == pytest.approx(78.0)


def test_analyze_inventory_cycle_classifies_new_orders_trend() -> None:
    """3 rising obs of AMTMNO → 'Rising'."""
    macro = {
        "ISRATIO": _is_df([1.42] * 14),
        "AMTMNO": _is_df([500.0, 505.0, 510.0] * 5),
    }
    sig = analyze_inventory_cycle(macro)
    assert sig is not None
    assert sig.new_orders_trend == "Rising"


def test_analyze_inventory_cycle_score_in_unit_interval() -> None:
    for series in (
        [1.42] * 14,                                              # NEUTRAL
        [1.30] * 14,                                              # RESTOCK lean stable
        list(range(150, 130, -2)) + [128, 126, 124, 122],          # RESTOCK falling-ish
    ):
        clean = [v / 100 for v in series] if max(series) > 5 else series
        sig = analyze_inventory_cycle({"ISRATIO": _is_df(clean)})
        if sig is not None:
            assert 0.0 <= sig.score <= 1.0


# ─── _classify_phase: each branch ───────────────────────────────────────────

def test_classify_phase_lean_falling_returns_restock_high_score() -> None:
    phase, desc, impl, score = _classify_phase(_LEAN_THRESHOLD - 0.02, "Falling", -0.03, None, None)
    assert phase == "RESTOCK"
    assert score == pytest.approx(0.85)


def test_classify_phase_lean_stable_returns_restock_moderate_score() -> None:
    phase, desc, impl, score = _classify_phase(_LEAN_THRESHOLD - 0.02, "Stable", 0.0, None, None)
    assert phase == "RESTOCK"
    assert score == pytest.approx(0.72)


def test_classify_phase_overstocked_rising_returns_drawdown_low_score() -> None:
    phase, desc, impl, score = _classify_phase(_OVERSTOCK_THRESHOLD + 0.02, "Rising", 0.03, None, None)
    assert phase == "DRAWDOWN"
    assert score == pytest.approx(0.25)


def test_classify_phase_overstocked_stable_returns_drawdown_moderate_score() -> None:
    phase, desc, impl, score = _classify_phase(_OVERSTOCK_THRESHOLD + 0.02, "Stable", 0.0, None, None)
    assert phase == "DRAWDOWN"
    assert score == pytest.approx(0.35)


def test_classify_phase_normalizing_falling_returns_building() -> None:
    """Above lean threshold but falling → BUILDING."""
    phase, desc, impl, score = _classify_phase(1.45, "Falling", -0.02, None, None)
    assert phase == "BUILDING"
    assert score == pytest.approx(0.60)


def test_classify_phase_normal_returns_neutral() -> None:
    phase, desc, impl, score = _classify_phase(1.42, "Stable", 0.0, None, None)
    assert phase == "NEUTRAL"
    assert score == pytest.approx(0.50)


def test_classify_phase_high_sentiment_boosts_score() -> None:
    """sentiment 90 → adj=+0.15; sentiment 60 → adj=-0.15."""
    _, _, _, hi = _classify_phase(1.42, "Stable", 0.0, 90.0, None)
    _, _, _, lo = _classify_phase(1.42, "Stable", 0.0, 60.0, None)
    assert hi > lo
    assert hi == pytest.approx(0.65, abs=0.01)
    assert lo == pytest.approx(0.35, abs=0.01)


def test_classify_phase_score_clamped_to_unit_interval() -> None:
    # Extreme positive sentiment → score capped at 1.0
    _, _, _, hi = _classify_phase(_LEAN_THRESHOLD - 0.02, "Falling", -0.03, 200.0, None)
    assert hi <= 1.0
    # Extreme negative sentiment + DRAWDOWN → floored at 0.0
    _, _, _, lo = _classify_phase(_OVERSTOCK_THRESHOLD + 0.02, "Rising", 0.03, -100.0, None)
    assert lo >= 0.0


# ─── get_inventory_score_for_engine ─────────────────────────────────────────

def test_get_inventory_score_for_engine_returns_half_when_signal_none() -> None:
    assert get_inventory_score_for_engine({}) == 0.5


def test_get_inventory_score_for_engine_propagates_signal_score() -> None:
    sig = analyze_inventory_cycle({"ISRATIO": _is_df([1.42] * 14)})
    assert sig is not None
    assert get_inventory_score_for_engine({"ISRATIO": _is_df([1.42] * 14)}) == sig.score


def test_get_inventory_score_for_engine_score_in_unit_interval() -> None:
    score = get_inventory_score_for_engine({"ISRATIO": _is_df([1.42] * 14)})
    assert 0.0 <= score <= 1.0
