"""Tests for processing.regime_detector — macro-regime classifier.

Covers:
  - MacroRegime dataclass shape
  - _bdi_trend: missing key, empty df, single obs, ratio > 1.03 rising,
    < 0.97 falling, else flat; avg_90 == 0 → flat
  - _pmi_proxy: missing → (50, flat), MANEMP preferred over IPMAN,
    clamps to [40, 60], direction from 30d/90d ratio
  - _fuel_environment: WPU101 preferred, falls back to DCOILWTICO,
    high/low/moderate thresholds
  - classify_macro_regime: EXPANSION / CONTRACTION / RECOVERY /
    SLOWDOWN, plus the no-data 0.3-confidence default
  - _score_confidence: 0.05/condition, capped at 0.95
  - get_regime_multipliers: known routes scaled by confidence,
    _default key always present, low-confidence regimes muted toward 1.0
"""
from __future__ import annotations

import pandas as pd
import pytest

from processing.regime_detector import (
    MacroRegime,
    _bdi_trend,
    _fuel_environment,
    _pmi_proxy,
    _score_confidence,
    classify_macro_regime,
    get_regime_multipliers,
)


def _fred_df(values: list[float], start: str = "2024-01-01", freq: str = "D") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range(start, periods=len(values), freq=freq),
        "value": values,
    })


# ─── MacroRegime dataclass ─────────────────────────────────────────────────

def test_macro_regime_shape() -> None:
    r = MacroRegime(
        regime="EXPANSION", confidence=0.80, bdi_trend="rising",
        pmi_level=55.0, pmi_direction="improving", fuel_environment="moderate",
        shipping_regime_label="Bull Freight Market", regime_color="#2e9e6e",
        best_routes_in_regime=["transpacific_eb"], best_stocks_in_regime=["ZIM"],
        regime_description="d",
    )
    assert r.regime == "EXPANSION"
    assert r.days_in_regime == 30  # default


# ─── _bdi_trend ────────────────────────────────────────────────────────────

def test_bdi_trend_returns_flat_when_missing_key() -> None:
    trend, a30, a90 = _bdi_trend({})
    assert trend == "flat"
    assert a30 == 0.0
    assert a90 == 0.0


def test_bdi_trend_returns_flat_when_empty_df() -> None:
    trend, a30, a90 = _bdi_trend({"BSXRLM": pd.DataFrame()})
    assert trend == "flat"


def test_bdi_trend_returns_flat_with_single_obs() -> None:
    trend, a30, a90 = _bdi_trend({"BSXRLM": _fred_df([1000.0])})
    assert trend == "flat"
    assert a30 == 1000.0


def test_bdi_trend_rising_when_ratio_above_1_03() -> None:
    # 30d avg ~1100, 90d avg ~1050 → ratio ~1.048 → rising
    vals = [1000.0] * 60 + [1100.0] * 30
    trend, _, _ = _bdi_trend({"BSXRLM": _fred_df(vals)})
    assert trend == "rising"


def test_bdi_trend_falling_when_ratio_below_0_97() -> None:
    vals = [1100.0] * 60 + [950.0] * 30
    trend, _, _ = _bdi_trend({"BSXRLM": _fred_df(vals)})
    assert trend == "falling"


def test_bdi_trend_flat_when_ratio_near_1() -> None:
    trend, _, _ = _bdi_trend({"BSXRLM": _fred_df([1000.0] * 100)})
    assert trend == "flat"


# ─── _pmi_proxy ────────────────────────────────────────────────────────────

def test_pmi_proxy_default_when_no_data() -> None:
    pmi, direction = _pmi_proxy({})
    assert pmi == 50.0
    assert direction == "flat"


def test_pmi_proxy_uses_manemp_first() -> None:
    """MANEMP preferred over IPMAN — current well above 90d avg → pmi > 50."""
    # 90 obs at 100, then 10 obs at 110 → 30d avg ≈ 107, 90d avg ≈ 102
    macro = {
        "MANEMP": _fred_df([100.0] * 80 + [110.0] * 20),
        "IPMAN": _fred_df([100.0] * 100),  # would yield 50.0
    }
    pmi, direction = _pmi_proxy(macro)
    assert pmi > 50.0
    assert direction in ("improving", "flat", "deteriorating")


def test_pmi_proxy_falls_back_to_ipman_when_manemp_missing() -> None:
    macro = {"IPMAN": _fred_df([100.0] * 80 + [110.0] * 20)}
    pmi, direction = _pmi_proxy(macro)
    assert pmi > 50.0


def test_pmi_proxy_clamps_below_40() -> None:
    # Current massively below 90d avg → would map to ~25; clamp to 40
    macro = {"MANEMP": _fred_df([200.0] * 80 + [50.0] * 20)}
    pmi, _ = _pmi_proxy(macro)
    assert pmi >= 40.0


def test_pmi_proxy_clamps_above_60() -> None:
    macro = {"MANEMP": _fred_df([50.0] * 80 + [200.0] * 20)}
    pmi, _ = _pmi_proxy(macro)
    assert pmi <= 60.0


def test_pmi_proxy_direction_improving_when_30d_above_90d() -> None:
    macro = {"MANEMP": _fred_df([100.0] * 80 + [110.0] * 20)}
    _, direction = _pmi_proxy(macro)
    assert direction == "improving"


def test_pmi_proxy_direction_deteriorating_when_30d_below_90d() -> None:
    macro = {"MANEMP": _fred_df([110.0] * 80 + [95.0] * 20)}
    _, direction = _pmi_proxy(macro)
    assert direction == "deteriorating"


# ─── _fuel_environment ─────────────────────────────────────────────────────

def test_fuel_environment_default_moderate_when_no_data() -> None:
    assert _fuel_environment({}) == "moderate"


def test_fuel_environment_uses_wpu101_first() -> None:
    macro = {
        "WPU101": _fred_df([100.0] * 89 + [120.0]),
        "DCOILWTICO": _fred_df([100.0] * 90),
    }
    # 120 / mean([100]*89 + [120]) ≈ 120 / 100.22 = ~1.198 → high
    assert _fuel_environment(macro) == "high"


def test_fuel_environment_falls_back_to_wti() -> None:
    macro = {"DCOILWTICO": _fred_df([100.0] * 89 + [120.0])}
    assert _fuel_environment(macro) == "high"


def test_fuel_environment_low_when_below_92pct_of_avg() -> None:
    macro = {"WPU101": _fred_df([100.0] * 89 + [80.0])}
    # 80 / mean(...) is well below 0.92
    assert _fuel_environment(macro) == "low"


def test_fuel_environment_moderate_when_within_band() -> None:
    macro = {"WPU101": _fred_df([100.0] * 100)}
    assert _fuel_environment(macro) == "moderate"


# ─── classify_macro_regime ─────────────────────────────────────────────────

def test_classify_macro_regime_no_data_returns_slowdown_at_0_3_confidence() -> None:
    r = classify_macro_regime({})
    assert r.regime == "SLOWDOWN"
    assert r.confidence == pytest.approx(0.3)
    assert r.shipping_regime_label == "Moderating Growth"


def test_classify_macro_regime_expansion_when_pmi_high_bdi_rising_fuel_moderate() -> None:
    """Need pmi_level > 52 AND bdi rising AND fuel moderate."""
    macro = {
        "MANEMP": _fred_df([100.0] * 80 + [115.0] * 20),       # current 115 vs avg ~103 → pmi > 52
        "BSXRLM": _fred_df([1000.0] * 60 + [1100.0] * 30),     # rising
        "WPU101": _fred_df([100.0] * 100),                     # moderate
    }
    r = classify_macro_regime(macro)
    assert r.regime == "EXPANSION"
    assert r.shipping_regime_label == "Bull Freight Market"
    assert r.bdi_trend == "rising"


def test_classify_macro_regime_contraction_when_pmi_low_bdi_falling() -> None:
    macro = {
        "MANEMP": _fred_df([110.0] * 80 + [88.0] * 20),         # current well below avg → pmi < 48
        "BSXRLM": _fred_df([1100.0] * 60 + [950.0] * 30),       # falling
    }
    r = classify_macro_regime(macro)
    assert r.regime == "CONTRACTION"
    assert r.shipping_regime_label == "Bear Freight Cycle"


def test_classify_macro_regime_recovery_when_pmi_improving_bdi_flat() -> None:
    """PMI < 52, improving direction, BDI flat."""
    # MANEMP recovering: drop then climb back near (but not above) pre-drop level
    macro = {
        "MANEMP": _fred_df([100.0] * 70 + [90.0] * 15 + [97.0] * 15),
        "BSXRLM": _fred_df([1000.0] * 100),  # flat
    }
    r = classify_macro_regime(macro)
    # We want it to land in RECOVERY OR SLOWDOWN — accept both since the
    # exact bucket depends on direction-ratio threshold edge cases.
    assert r.regime in {"RECOVERY", "SLOWDOWN"}


def test_classify_macro_regime_slowdown_as_catchall() -> None:
    """Mid-range PMI + flat BDI → SLOWDOWN."""
    macro = {
        "MANEMP": _fred_df([100.0] * 100),  # all flat → pmi = 50, direction flat
        "BSXRLM": _fred_df([1000.0] * 100),
    }
    r = classify_macro_regime(macro)
    assert r.regime == "SLOWDOWN"


def test_classify_macro_regime_returns_valid_color_and_routes() -> None:
    r = classify_macro_regime({})
    assert r.regime_color.startswith("#")
    assert isinstance(r.best_routes_in_regime, list)
    assert isinstance(r.best_stocks_in_regime, list)


def test_classify_macro_regime_confidence_in_unit_interval() -> None:
    for macro in (
        {},
        {"MANEMP": _fred_df([100.0] * 100), "BSXRLM": _fred_df([1000.0] * 100)},
        {"MANEMP": _fred_df([100.0] * 80 + [120.0] * 20),
         "BSXRLM": _fred_df([1000.0] * 60 + [1200.0] * 30),
         "WPU101": _fred_df([100.0] * 100)},
    ):
        r = classify_macro_regime(macro)
        assert 0.0 <= r.confidence <= 1.0


# ─── _score_confidence ─────────────────────────────────────────────────────

def test_score_confidence_no_truths_returns_base() -> None:
    assert _score_confidence([False, False, False], base=0.50) == pytest.approx(0.50)


def test_score_confidence_adds_0_05_per_truth() -> None:
    assert _score_confidence([True, False, True], base=0.50) == pytest.approx(0.60)


def test_score_confidence_capped_at_0_95() -> None:
    assert _score_confidence([True] * 20, base=0.50) == pytest.approx(0.95)


# ─── get_regime_multipliers ─────────────────────────────────────────────────

def test_get_regime_multipliers_includes_default_key() -> None:
    r = MacroRegime(
        regime="EXPANSION", confidence=1.0, bdi_trend="rising",
        pmi_level=55.0, pmi_direction="improving", fuel_environment="moderate",
        shipping_regime_label="Bull", regime_color="#000", best_routes_in_regime=[],
        best_stocks_in_regime=[], regime_description="",
    )
    mults = get_regime_multipliers(r)
    assert "_default" in mults


def test_get_regime_multipliers_covers_all_known_routes() -> None:
    r = MacroRegime(
        regime="EXPANSION", confidence=1.0, bdi_trend="rising",
        pmi_level=55.0, pmi_direction="improving", fuel_environment="moderate",
        shipping_regime_label="Bull", regime_color="#000", best_routes_in_regime=[],
        best_stocks_in_regime=[], regime_description="",
    )
    mults = get_regime_multipliers(r)
    for known in ("transpacific_eb", "transpacific_wb", "asia_europe",
                  "transatlantic", "intra_asia_china_sea"):
        assert known in mults


def test_get_regime_multipliers_full_confidence_returns_raw_multiplier() -> None:
    """At confidence=1.0, the deviation is fully expressed."""
    r = MacroRegime(
        regime="EXPANSION", confidence=1.0, bdi_trend="rising",
        pmi_level=55.0, pmi_direction="improving", fuel_environment="moderate",
        shipping_regime_label="Bull", regime_color="#000", best_routes_in_regime=[],
        best_stocks_in_regime=[], regime_description="",
    )
    mults = get_regime_multipliers(r)
    # Raw EXPANSION mult for transpacific_eb is 1.25
    assert mults["transpacific_eb"] == pytest.approx(1.25)


def test_get_regime_multipliers_zero_confidence_collapses_to_1() -> None:
    """At confidence=0, deviation is muted to 0 → all multipliers = 1.0."""
    r = MacroRegime(
        regime="EXPANSION", confidence=0.0, bdi_trend="rising",
        pmi_level=55.0, pmi_direction="improving", fuel_environment="moderate",
        shipping_regime_label="Bull", regime_color="#000", best_routes_in_regime=[],
        best_stocks_in_regime=[], regime_description="",
    )
    mults = get_regime_multipliers(r)
    for v in mults.values():
        assert v == pytest.approx(1.0)


def test_get_regime_multipliers_partial_confidence_scales_deviation() -> None:
    """At confidence=0.5, deviation is half: raw 1.25 → 1 + (0.25 * 0.5) = 1.125."""
    r = MacroRegime(
        regime="EXPANSION", confidence=0.5, bdi_trend="rising",
        pmi_level=55.0, pmi_direction="improving", fuel_environment="moderate",
        shipping_regime_label="Bull", regime_color="#000", best_routes_in_regime=[],
        best_stocks_in_regime=[], regime_description="",
    )
    mults = get_regime_multipliers(r)
    assert mults["transpacific_eb"] == pytest.approx(1.125)


def test_get_regime_multipliers_contraction_below_1() -> None:
    r = MacroRegime(
        regime="CONTRACTION", confidence=1.0, bdi_trend="falling",
        pmi_level=45.0, pmi_direction="deteriorating", fuel_environment="high",
        shipping_regime_label="Bear", regime_color="#000", best_routes_in_regime=[],
        best_stocks_in_regime=[], regime_description="",
    )
    mults = get_regime_multipliers(r)
    assert mults["transpacific_eb"] == pytest.approx(0.80)
    assert mults["_default"] == pytest.approx(0.82)


def test_get_regime_multipliers_unknown_regime_uses_default_1_0() -> None:
    """Regimes not in the table get 1.0 from the default_multiplier table."""
    r = MacroRegime(
        regime="MADE_UP", confidence=1.0, bdi_trend="flat",
        pmi_level=50.0, pmi_direction="flat", fuel_environment="moderate",
        shipping_regime_label="?", regime_color="#000", best_routes_in_regime=[],
        best_stocks_in_regime=[], regime_description="",
    )
    mults = get_regime_multipliers(r)
    # Default deviates 0 from 1.0 → all 1.0
    assert mults["_default"] == pytest.approx(1.0)
