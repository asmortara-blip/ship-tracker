"""Tests for processing.trade_finance — trade-finance indicators + risk scoring.

Covers:
  - TRADE_FINANCE_INDICATORS catalog:
      * every entry carries the keys the dataclass needs
      * signals are one of {BULLISH, BEARISH, NEUTRAL}
      * shipping_lead_weeks > 0
  - TradeFinanceIndicator / TradeFinanceRiskScore dataclasses: field round-trip
  - _classify_signal:
      * |yoy| < threshold → NEUTRAL
      * positive yoy, not inverse → BULLISH
      * negative yoy, not inverse → BEARISH
      * positive yoy, inverse → BEARISH (inverse flips sign)
      * negative yoy, inverse → BULLISH
      * custom threshold respected
  - build_trade_finance_indicators:
      * one dataclass per catalog entry
      * fields are carried through unchanged
  - compute_trade_finance_composite:
      * empty list → composite_score=0.5, dominant_signal NEUTRAL
      * None default → falls back to building indicators
      * all bullish → composite_score=1.0, dominant BULLISH
      * all bearish → composite_score=0.0, dominant BEARISH
      * tied counts → dominant NEUTRAL
      * counts add up to total
  - compute_regional_finance_risk:
      * returns one entry per _REGIONAL_RISK_DATA row
      * sorted by score descending
      * scores in [0, 1]
      * top region is Russia/CIS (highest in catalog)
      * each entry has non-empty affected_routes
  - compute_interest_rate_impact_on_shipping:
      * excess_rate_pp = max(0, rate - 2.5), clamped at 0 below neutral
      * demand impact = -excess_pp * 2.0
      * cumulative impact uses cycle start of 0.08%
      * scenario tier boundaries (5.0, 3.5, 2.5)
      * transmission lag dict has low=24, high=52
      * affected_routes always present + non-empty
      * narrative contains the formatted rate
"""
from __future__ import annotations

import pytest

from processing.trade_finance import (
    TRADE_FINANCE_INDICATORS,
    TradeFinanceIndicator,
    TradeFinanceRiskScore,
    _REGIONAL_RISK_DATA,
    _classify_signal,
    build_trade_finance_indicators,
    compute_interest_rate_impact_on_shipping,
    compute_regional_finance_risk,
    compute_trade_finance_composite,
)


# ─── Catalog shape ──────────────────────────────────────────────────────────

def test_indicator_catalog_has_required_keys() -> None:
    required = {
        "indicator_name", "current_value", "yoy_change_pct",
        "signal", "shipping_lead_weeks", "description", "data_source",
    }
    for key, meta in TRADE_FINANCE_INDICATORS.items():
        assert required <= set(meta.keys()), f"{key} missing keys"


def test_indicator_catalog_signals_are_valid() -> None:
    """Every catalog signal is one of BULLISH / BEARISH / NEUTRAL."""
    allowed = {"BULLISH", "BEARISH", "NEUTRAL"}
    for key, meta in TRADE_FINANCE_INDICATORS.items():
        assert meta["signal"] in allowed, f"{key} has invalid signal"


def test_indicator_catalog_shipping_lead_weeks_positive() -> None:
    for key, meta in TRADE_FINANCE_INDICATORS.items():
        assert meta["shipping_lead_weeks"] > 0, f"{key} lead weeks must be > 0"


def test_indicator_catalog_has_ten_entries() -> None:
    """Module docstring + signal mix premise depend on the 10-indicator catalog."""
    assert len(TRADE_FINANCE_INDICATORS) == 10


def test_indicator_catalog_descriptions_nonempty() -> None:
    for key, meta in TRADE_FINANCE_INDICATORS.items():
        assert isinstance(meta["description"], str)
        assert len(meta["description"]) > 20


# ─── Dataclasses ────────────────────────────────────────────────────────────

def test_trade_finance_indicator_dataclass_shape() -> None:
    ind = TradeFinanceIndicator(
        indicator_name="Test",
        current_value=1.0,
        yoy_change_pct=2.0,
        signal="BULLISH",
        shipping_lead_weeks=4,
        description="d",
        data_source="s",
    )
    assert ind.indicator_name == "Test"
    assert ind.current_value == pytest.approx(1.0)
    assert ind.signal == "BULLISH"


def test_trade_finance_risk_score_dataclass_shape() -> None:
    rs = TradeFinanceRiskScore(
        region="Test",
        score=0.5,
        primary_risk="risk",
        affected_routes=["A", "B"],
        rate_impact_pct=1.2,
    )
    assert rs.region == "Test"
    assert rs.score == pytest.approx(0.5)
    assert rs.affected_routes == ["A", "B"]


# ─── _classify_signal ───────────────────────────────────────────────────────

def test_classify_signal_neutral_below_default_threshold() -> None:
    """|yoy| < 2.0 (default) → NEUTRAL."""
    assert _classify_signal(0.0) == "NEUTRAL"
    assert _classify_signal(1.9) == "NEUTRAL"
    assert _classify_signal(-1.9) == "NEUTRAL"


def test_classify_signal_positive_yoy_is_bullish() -> None:
    """Positive YoY (non-inverse) → BULLISH."""
    assert _classify_signal(5.0) == "BULLISH"


def test_classify_signal_negative_yoy_is_bearish() -> None:
    """Negative YoY (non-inverse) → BEARISH."""
    assert _classify_signal(-5.0) == "BEARISH"


def test_classify_signal_inverse_flips_positive_to_bearish() -> None:
    """For an inverse series (e.g. cost where higher is worse), +YoY → BEARISH."""
    assert _classify_signal(5.0, inverse=True) == "BEARISH"


def test_classify_signal_inverse_flips_negative_to_bullish() -> None:
    """For an inverse series, -YoY → BULLISH (costs dropping is good)."""
    assert _classify_signal(-5.0, inverse=True) == "BULLISH"


def test_classify_signal_custom_threshold_respected() -> None:
    """With threshold=10, |yoy| < 10 stays NEUTRAL."""
    assert _classify_signal(5.0, threshold=10.0) == "NEUTRAL"
    assert _classify_signal(11.0, threshold=10.0) == "BULLISH"


def test_classify_signal_exact_threshold_is_not_neutral() -> None:
    """|yoy| == threshold is NOT < threshold → non-neutral."""
    # Default threshold=2.0; yoy=2.0 → not strictly less → BULLISH
    assert _classify_signal(2.0) == "BULLISH"


# ─── build_trade_finance_indicators ─────────────────────────────────────────

def test_build_indicators_one_per_catalog_entry() -> None:
    out = build_trade_finance_indicators()
    assert len(out) == len(TRADE_FINANCE_INDICATORS)


def test_build_indicators_returns_dataclasses() -> None:
    out = build_trade_finance_indicators()
    for ind in out:
        assert isinstance(ind, TradeFinanceIndicator)


def test_build_indicators_carries_catalog_fields_through() -> None:
    out = build_trade_finance_indicators()
    names = {ind.indicator_name for ind in out}
    expected = {meta["indicator_name"] for meta in TRADE_FINANCE_INDICATORS.values()}
    assert names == expected


def test_build_indicators_preserves_signals() -> None:
    out = build_trade_finance_indicators()
    # Match each indicator to its catalog entry by name and compare signal
    by_name = {ind.indicator_name: ind for ind in out}
    for key, meta in TRADE_FINANCE_INDICATORS.items():
        assert by_name[meta["indicator_name"]].signal == meta["signal"]


# ─── compute_trade_finance_composite ───────────────────────────────────────

def test_composite_empty_list_returns_neutral_half() -> None:
    out = compute_trade_finance_composite([])
    assert out["composite_score"] == pytest.approx(0.5)
    assert out["dominant_signal"] == "NEUTRAL"
    assert out["bullish_count"] == 0
    assert out["bearish_count"] == 0
    assert out["neutral_count"] == 0


def test_composite_none_default_falls_back_to_builder() -> None:
    """Passing no argument should hydrate from the catalog."""
    out = compute_trade_finance_composite()
    assert out["bullish_count"] + out["bearish_count"] + out["neutral_count"] == len(
        TRADE_FINANCE_INDICATORS
    )


def _ind(signal: str) -> TradeFinanceIndicator:
    return TradeFinanceIndicator(
        indicator_name="x", current_value=0.0, yoy_change_pct=0.0,
        signal=signal, shipping_lead_weeks=1, description="d", data_source="s",
    )


def test_composite_all_bullish_returns_one() -> None:
    inds = [_ind("BULLISH") for _ in range(5)]
    out = compute_trade_finance_composite(inds)
    assert out["composite_score"] == pytest.approx(1.0)
    assert out["dominant_signal"] == "BULLISH"
    assert out["bullish_count"] == 5


def test_composite_all_bearish_returns_zero() -> None:
    inds = [_ind("BEARISH") for _ in range(4)]
    out = compute_trade_finance_composite(inds)
    assert out["composite_score"] == pytest.approx(0.0)
    assert out["dominant_signal"] == "BEARISH"
    assert out["bearish_count"] == 4


def test_composite_tied_counts_dominant_is_neutral() -> None:
    inds = [_ind("BULLISH"), _ind("BEARISH")]
    out = compute_trade_finance_composite(inds)
    assert out["dominant_signal"] == "NEUTRAL"
    assert out["composite_score"] == pytest.approx(0.5)


def test_composite_neutral_only_returns_half() -> None:
    inds = [_ind("NEUTRAL") for _ in range(3)]
    out = compute_trade_finance_composite(inds)
    assert out["composite_score"] == pytest.approx(0.5)
    assert out["dominant_signal"] == "NEUTRAL"
    assert out["neutral_count"] == 3


def test_composite_counts_sum_to_total() -> None:
    inds = [_ind("BULLISH"), _ind("BEARISH"), _ind("NEUTRAL"), _ind("BULLISH")]
    out = compute_trade_finance_composite(inds)
    total = out["bullish_count"] + out["bearish_count"] + out["neutral_count"]
    assert total == 4


def test_composite_score_in_unit_interval() -> None:
    """For any signal mix, composite_score stays in [0, 1]."""
    for mix in (["BULLISH"], ["BEARISH"], ["NEUTRAL"],
                ["BULLISH", "BEARISH"], ["BULLISH"] * 3 + ["BEARISH"]):
        out = compute_trade_finance_composite([_ind(s) for s in mix])
        assert 0.0 <= out["composite_score"] <= 1.0


# ─── compute_regional_finance_risk ─────────────────────────────────────────

def test_regional_finance_risk_one_entry_per_region() -> None:
    out = compute_regional_finance_risk()
    assert len(out) == len(_REGIONAL_RISK_DATA)


def test_regional_finance_risk_returns_dataclasses() -> None:
    out = compute_regional_finance_risk()
    for r in out:
        assert isinstance(r, TradeFinanceRiskScore)


def test_regional_finance_risk_sorted_descending() -> None:
    """Output must be sorted by score descending."""
    out = compute_regional_finance_risk()
    scores = [r.score for r in out]
    assert scores == sorted(scores, reverse=True)


def test_regional_finance_risk_top_is_russia_cis() -> None:
    """Highest catalog risk (0.95) is Russia / CIS."""
    out = compute_regional_finance_risk()
    assert out[0].region == "Russia / CIS"


def test_regional_finance_risk_scores_in_unit_interval() -> None:
    out = compute_regional_finance_risk()
    for r in out:
        assert 0.0 <= r.score <= 1.0


def test_regional_finance_risk_affected_routes_nonempty() -> None:
    out = compute_regional_finance_risk()
    for r in out:
        assert len(r.affected_routes) > 0
        assert all(isinstance(rt, str) for rt in r.affected_routes)


def test_regional_finance_risk_rate_impact_nonnegative() -> None:
    """Rate-impact uplift is reported as a positive %; never negative."""
    out = compute_regional_finance_risk()
    for r in out:
        assert r.rate_impact_pct >= 0.0


def test_regional_finance_risk_north_america_is_lowest() -> None:
    """North America / Western Europe is the lowest-risk region in the catalog."""
    out = compute_regional_finance_risk()
    assert out[-1].region == "North America / Western Europe"


# ─── compute_interest_rate_impact_on_shipping ──────────────────────────────

def test_rate_impact_keys_present() -> None:
    out = compute_interest_rate_impact_on_shipping(5.0)
    required = {
        "current_rate_pct", "neutral_rate_pct", "excess_rate_pp",
        "estimated_demand_impact_pct", "cumulative_impact_since_2022_pct",
        "transmission_lag_weeks", "affected_routes", "scenario_label", "narrative",
    }
    assert required <= set(out.keys())


def test_rate_impact_neutral_rate_is_2_5() -> None:
    out = compute_interest_rate_impact_on_shipping(5.0)
    assert out["neutral_rate_pct"] == pytest.approx(2.5)


def test_rate_impact_excess_pp_equals_rate_minus_neutral() -> None:
    out = compute_interest_rate_impact_on_shipping(5.0)
    # 5.0 - 2.5 = 2.5pp
    assert out["excess_rate_pp"] == pytest.approx(2.5)


def test_rate_impact_excess_clipped_at_zero_below_neutral() -> None:
    """When rate < neutral, excess_pp clamps to 0 and demand impact is 0."""
    out = compute_interest_rate_impact_on_shipping(1.0)
    assert out["excess_rate_pp"] == pytest.approx(0.0)
    assert out["estimated_demand_impact_pct"] == pytest.approx(0.0)


def test_rate_impact_demand_impact_uses_2pct_elasticity() -> None:
    """At rate=5.0, excess=2.5pp → demand impact = -5.0%."""
    out = compute_interest_rate_impact_on_shipping(5.0)
    assert out["estimated_demand_impact_pct"] == pytest.approx(-5.0)


def test_rate_impact_demand_impact_is_negative_when_restrictive() -> None:
    out = compute_interest_rate_impact_on_shipping(6.0)
    assert out["estimated_demand_impact_pct"] < 0.0


def test_rate_impact_cumulative_uses_cycle_start_0_08() -> None:
    """Cumulative impact = -(rate - 0.08) * 2 — measured from March 2022 trough."""
    out = compute_interest_rate_impact_on_shipping(5.08)
    # (5.08 - 0.08) * 2 = 10.0
    assert out["cumulative_impact_since_2022_pct"] == pytest.approx(-10.0)


def test_rate_impact_cumulative_at_low_rate_still_nonpositive() -> None:
    """At rate = cycle start, cumulative impact = 0."""
    out = compute_interest_rate_impact_on_shipping(0.08)
    assert out["cumulative_impact_since_2022_pct"] == pytest.approx(0.0)


def test_rate_impact_cumulative_clamped_below_cycle_start() -> None:
    """Below the cycle start, cumulative_excess clips to 0."""
    out = compute_interest_rate_impact_on_shipping(0.0)
    assert out["cumulative_impact_since_2022_pct"] == pytest.approx(0.0)


def test_rate_impact_scenario_highly_restrictive_at_5() -> None:
    out = compute_interest_rate_impact_on_shipping(5.0)
    assert "HIGHLY RESTRICTIVE" in out["scenario_label"]


def test_rate_impact_scenario_restrictive_between_3_5_and_5() -> None:
    out = compute_interest_rate_impact_on_shipping(4.0)
    assert out["scenario_label"].startswith("RESTRICTIVE")


def test_rate_impact_scenario_near_neutral_between_2_5_and_3_5() -> None:
    out = compute_interest_rate_impact_on_shipping(3.0)
    assert "NEAR NEUTRAL" in out["scenario_label"]


def test_rate_impact_scenario_accommodative_below_2_5() -> None:
    out = compute_interest_rate_impact_on_shipping(1.0)
    assert "ACCOMMODATIVE" in out["scenario_label"]


def test_rate_impact_scenario_boundary_at_5_pct_inclusive() -> None:
    """rate == 5.0 belongs to HIGHLY RESTRICTIVE (>= 5.0)."""
    out = compute_interest_rate_impact_on_shipping(5.0)
    assert "HIGHLY RESTRICTIVE" in out["scenario_label"]


def test_rate_impact_scenario_boundary_at_3_5_inclusive() -> None:
    """rate == 3.5 belongs to RESTRICTIVE (>= 3.5)."""
    out = compute_interest_rate_impact_on_shipping(3.5)
    assert out["scenario_label"].startswith("RESTRICTIVE")
    # And not HIGHLY RESTRICTIVE
    assert "HIGHLY" not in out["scenario_label"]


def test_rate_impact_scenario_boundary_at_2_5_inclusive() -> None:
    """rate == 2.5 belongs to NEAR NEUTRAL (>= 2.5)."""
    out = compute_interest_rate_impact_on_shipping(2.5)
    assert "NEAR NEUTRAL" in out["scenario_label"]


def test_rate_impact_transmission_lag_fixed_24_52() -> None:
    out = compute_interest_rate_impact_on_shipping(5.0)
    assert out["transmission_lag_weeks"] == {"low": 24, "high": 52}


def test_rate_impact_affected_routes_nonempty() -> None:
    out = compute_interest_rate_impact_on_shipping(5.0)
    assert len(out["affected_routes"]) > 0
    assert "TRANSPACIFIC_EB" in out["affected_routes"]


def test_rate_impact_narrative_contains_formatted_rate() -> None:
    out = compute_interest_rate_impact_on_shipping(5.25)
    # round(5.25, 2) == 5.25 → "5.25" should appear
    assert "5.25" in out["narrative"]


def test_rate_impact_narrative_mentions_cycle_start_label() -> None:
    out = compute_interest_rate_impact_on_shipping(5.0)
    assert "March 2022" in out["narrative"]


def test_rate_impact_current_rate_round_tripped() -> None:
    """current_rate_pct in output is rounded to 3dp."""
    out = compute_interest_rate_impact_on_shipping(5.123456)
    assert out["current_rate_pct"] == pytest.approx(5.123)
