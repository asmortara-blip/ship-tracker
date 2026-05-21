"""Tests for processing.leading_indicators — composite shipping-demand score.

Covers:
  - LeadingIndicator dataclass shape
  - LEADING_INDICATORS catalog: every entry has the keys used by builders
  - _latest_two: empty df / no value col / single value
  - _change_pct: zero divisor returns 0; normal positive / negative
  - _classify_signal: under-threshold → NEUTRAL; positive non-inverse → BULLISH;
    positive inverse → BEARISH; etc
  - _signal_weight: BULLISH=+1 / BEARISH=-1 / NEUTRAL=0
  - build_leading_indicators: empty macro data → one indicator per series,
    all NEUTRAL with current=prev=0
  - compute_leading_indicator_score: empty → composite 0.5; forecast tiers
    (EXPANSION / CONTRACTION / STABLE); composite ∈ [0, 1]; weighted_signal ∈ [-1, +1]
  - build_lead_lag_matrix: empty → NaN rows; with benchmark series produces
    rows × 6 lag columns
  - get_recession_probability: empty → 0.0; Sahm rule firing; claims slope
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from processing.leading_indicators import (
    LEADING_INDICATORS,
    LeadingIndicator,
    _change_pct,
    _classify_signal,
    _latest_two,
    _signal_weight,
    build_lead_lag_matrix,
    build_leading_indicators,
    compute_leading_indicator_score,
    get_recession_probability,
)


def _fred_df(values: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range(start, periods=len(values), freq="MS"),
        "value": values,
    })


# ─── Dataclass + catalog ────────────────────────────────────────────────────

def test_leading_indicator_shape() -> None:
    ind = LeadingIndicator(
        series_id="X", name="x", current_value=1.0, previous_value=0.9,
        change_pct=11.1, signal="BULLISH", shipping_implication="i",
        lead_time_weeks=4, weight=0.1, data_frequency="Monthly",
    )
    assert ind.signal == "BULLISH"


def test_leading_indicators_catalog_has_required_keys() -> None:
    required = {"name", "lead_time_weeks", "weight", "data_frequency",
                "inverse_signal", "shipping_implication"}
    for sid, meta in LEADING_INDICATORS.items():
        assert required <= set(meta.keys()), f"{sid} missing keys"
        assert isinstance(meta["inverse_signal"], bool)


def test_leading_indicators_weights_sum_finite_and_positive() -> None:
    """The dataclass docstring claims weights 'sum to ~1.0'. They actually
    sum to ~1.20 (over-allocated by 20%) — but compute_leading_indicator_score
    normalizes by weight_total so it doesn't matter functionally. Pinning the
    actual sum so any future intentional rebalancing trips the test."""
    total = sum(m["weight"] for m in LEADING_INDICATORS.values())
    assert 1.10 <= total <= 1.30


# ─── _latest_two ────────────────────────────────────────────────────────────

def test_latest_two_returns_zeros_for_empty() -> None:
    assert _latest_two(pd.DataFrame()) == (0.0, 0.0)


def test_latest_two_returns_zeros_when_no_value_column() -> None:
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=3)})
    assert _latest_two(df) == (0.0, 0.0)


def test_latest_two_returns_zeros_for_all_nan() -> None:
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=3),
                       "value": [float("nan")] * 3})
    assert _latest_two(df) == (0.0, 0.0)


def test_latest_two_single_value_returns_current_twice() -> None:
    df = _fred_df([42.0])
    cur, prev = _latest_two(df)
    assert cur == 42.0
    assert prev == 42.0


def test_latest_two_sorts_by_date_when_present() -> None:
    """Unsorted input is sorted by date — latest comes last."""
    df = _fred_df([100.0, 110.0, 120.0])
    df = df.iloc[[2, 0, 1]]  # shuffle
    cur, prev = _latest_two(df)
    assert cur == 120.0
    assert prev == 110.0


# ─── _change_pct ────────────────────────────────────────────────────────────

def test_change_pct_zero_previous_returns_zero() -> None:
    assert _change_pct(100.0, 0.0) == 0.0


def test_change_pct_positive_growth() -> None:
    assert _change_pct(110.0, 100.0) == pytest.approx(10.0)


def test_change_pct_negative_growth() -> None:
    assert _change_pct(90.0, 100.0) == pytest.approx(-10.0)


def test_change_pct_uses_abs_for_denominator() -> None:
    assert _change_pct(10.0, -10.0) == pytest.approx(200.0)


# ─── _classify_signal ───────────────────────────────────────────────────────

def test_classify_signal_under_threshold_is_neutral() -> None:
    assert _classify_signal(0.3, inverse=False) == "NEUTRAL"
    assert _classify_signal(-0.3, inverse=False) == "NEUTRAL"


def test_classify_signal_positive_non_inverse_is_bullish() -> None:
    assert _classify_signal(2.0, inverse=False) == "BULLISH"


def test_classify_signal_positive_inverse_is_bearish() -> None:
    """UNRATE rising = bearish for shipping (inverse=True)."""
    assert _classify_signal(2.0, inverse=True) == "BEARISH"


def test_classify_signal_negative_non_inverse_is_bearish() -> None:
    assert _classify_signal(-2.0, inverse=False) == "BEARISH"


def test_classify_signal_negative_inverse_is_bullish() -> None:
    """UNRATE falling = bullish for shipping."""
    assert _classify_signal(-2.0, inverse=True) == "BULLISH"


def test_classify_signal_custom_threshold() -> None:
    assert _classify_signal(1.5, inverse=False, threshold=2.0) == "NEUTRAL"
    assert _classify_signal(2.5, inverse=False, threshold=2.0) == "BULLISH"


# ─── _signal_weight ─────────────────────────────────────────────────────────

def test_signal_weight_known_signals() -> None:
    assert _signal_weight("BULLISH") == 1.0
    assert _signal_weight("BEARISH") == -1.0
    assert _signal_weight("NEUTRAL") == 0.0


def test_signal_weight_unknown_signal_returns_zero() -> None:
    assert _signal_weight("WHATEVER") == 0.0


# ─── build_leading_indicators ──────────────────────────────────────────────

def test_build_leading_indicators_empty_data_returns_one_per_catalog() -> None:
    out = build_leading_indicators({})
    assert len(out) == len(LEADING_INDICATORS)
    for ind in out:
        assert ind.current_value == 0.0
        assert ind.previous_value == 0.0
        assert ind.signal == "NEUTRAL"


def test_build_leading_indicators_populates_real_values() -> None:
    macro = {"IPMAN": _fred_df([100.0, 105.0])}
    out = build_leading_indicators(macro)
    ipman = next(i for i in out if i.series_id == "IPMAN")
    assert ipman.current_value == 105.0
    assert ipman.previous_value == 100.0
    assert ipman.change_pct == pytest.approx(5.0)
    assert ipman.signal == "BULLISH"


def test_build_leading_indicators_respects_inverse_flag() -> None:
    """UNRATE rising → BEARISH for shipping demand."""
    macro = {"UNRATE": _fred_df([4.0, 5.0])}
    out = build_leading_indicators(macro)
    unrate = next(i for i in out if i.series_id == "UNRATE")
    assert unrate.signal == "BEARISH"


# ─── compute_leading_indicator_score ────────────────────────────────────────

def test_compute_leading_indicator_score_empty_returns_neutral() -> None:
    """With no data, build_leading_indicators still returns NEUTRAL entries,
    so weighted_signal=0 → composite 0.5 → 'STABLE'."""
    out = compute_leading_indicator_score({})
    assert out["composite_score"] == 0.5
    assert out["four_week_forecast"] == "STABLE"
    assert out["weighted_signal"] == 0.0


def test_compute_leading_indicator_score_bullish_macro_produces_expansion() -> None:
    """Make several non-inverse series rise sharply → composite > 0.5."""
    macro = {sid: _fred_df([100.0, 110.0]) for sid in
             ("IPMAN", "AMTMNO", "MRTSSM44000USS", "MANEMP", "UMCSENT")}
    out = compute_leading_indicator_score(macro)
    assert out["composite_score"] > 0.5
    assert out["four_week_forecast"] in {"EXPANSION", "STABLE"}
    assert out["bullish_count"] >= 1


def test_compute_leading_indicator_score_bearish_macro_produces_contraction() -> None:
    """Make several non-inverse series fall sharply → composite < 0.5."""
    macro = {sid: _fred_df([100.0, 80.0]) for sid in
             ("IPMAN", "AMTMNO", "MRTSSM44000USS", "MANEMP", "UMCSENT", "HOUST")}
    out = compute_leading_indicator_score(macro)
    assert out["composite_score"] < 0.5
    assert out["four_week_forecast"] in {"CONTRACTION", "STABLE"}


def test_compute_leading_indicator_score_composite_in_unit_interval() -> None:
    """Whatever the input, composite ∈ [0, 1] and weighted_signal ∈ [-1, +1]."""
    for macro in (
        {},
        {"IPMAN": _fred_df([100.0, 110.0])},
        {sid: _fred_df([100.0, 200.0]) for sid in LEADING_INDICATORS},
    ):
        out = compute_leading_indicator_score(macro)
        assert 0.0 <= out["composite_score"] <= 1.0
        assert -1.0 <= out["weighted_signal"] <= 1.0


def test_compute_leading_indicator_score_returns_top_3_lists() -> None:
    """top_bullish_indicators / top_bearish_indicators contain up to 3 names."""
    macro = {sid: _fred_df([100.0, 110.0]) for sid in LEADING_INDICATORS}
    out = compute_leading_indicator_score(macro)
    assert len(out["top_bullish_indicators"]) <= 3
    assert len(out["top_bearish_indicators"]) <= 3


# ─── build_lead_lag_matrix ─────────────────────────────────────────────────

def test_build_lead_lag_matrix_empty_returns_nan_grid() -> None:
    df = build_lead_lag_matrix({}, None)
    # One row per catalog indicator, six lag columns
    assert len(df) == len(LEADING_INDICATORS)
    assert list(df.columns) == ["Lag 0wk", "Lag 2wk", "Lag 4wk",
                                "Lag 6wk", "Lag 8wk", "Lag 12wk"]
    # Every cell NaN (no benchmark to correlate against)
    assert df.isna().all().all()


def test_build_lead_lag_matrix_with_benchmark_yields_correlations() -> None:
    """When BSXRLM + at least one indicator share a date overlap, we get
    finite correlations for at least lag 0."""
    rng = pd.date_range("2024-01-01", periods=120, freq="D")
    bench = pd.DataFrame({"date": rng, "value": list(range(120))})
    ipman = pd.DataFrame({"date": rng, "value": list(range(120))})  # perfectly correlated
    macro = {"BSXRLM": bench, "IPMAN": ipman}
    df = build_lead_lag_matrix(macro, None)
    # IPMAN's lag-0 cell should be a finite number close to 1.0
    ipman_row = df.loc["Industrial Production — Manufacturing"]
    assert math.isfinite(ipman_row["Lag 0wk"])
    assert abs(ipman_row["Lag 0wk"]) == pytest.approx(1.0, abs=0.01)


def test_build_lead_lag_matrix_freight_data_overrides_macro_bdi() -> None:
    """When freight_data carries BDI, it's used instead of macro BSXRLM."""
    rng = pd.date_range("2024-01-01", periods=120, freq="D")
    macro_bdi = pd.DataFrame({"date": rng, "value": list(range(120))})
    freight_bdi = pd.DataFrame({"date": rng, "value": [v * -1 for v in range(120)]})
    ipman = pd.DataFrame({"date": rng, "value": list(range(120))})
    macro = {"BSXRLM": macro_bdi, "IPMAN": ipman}
    freight = {"BDI": freight_bdi}
    df = build_lead_lag_matrix(macro, freight)
    # With the inverted freight BDI, IPMAN's lag-0 corr should be near -1.
    ipman_row = df.loc["Industrial Production — Manufacturing"]
    assert ipman_row["Lag 0wk"] == pytest.approx(-1.0, abs=0.01)


# ─── get_recession_probability ─────────────────────────────────────────────

def test_get_recession_probability_empty_data_returns_zero() -> None:
    assert get_recession_probability({}) == 0.0


def test_get_recession_probability_in_unit_interval() -> None:
    macro = {"UNRATE": _fred_df([3.5] * 12 + [4.5])}
    prob = get_recession_probability(macro)
    assert 0.0 <= prob <= 1.0


def test_get_recession_probability_sahm_rule_fires_on_unrate_rise() -> None:
    """3-month avg ≥ 1.0 pp above 12-month low → Sahm component caps at 1.0."""
    macro = {"UNRATE": _fred_df([3.5] * 12 + [5.0, 5.0, 5.0])}
    prob = get_recession_probability(macro)
    # 0.65 * 1.0 + 0.35 * 0.0 (no claims data) = 0.65
    assert prob >= 0.50


def test_get_recession_probability_claims_slope_contribution() -> None:
    """Sharp rise in IC4WSA → claims contribution adds to prob."""
    macro = {
        "UNRATE": _fred_df([4.0] * 12 + [4.0]),  # no Sahm fire
        "IC4WSA": _fred_df([200_000] * 4 + [260_000] * 4),  # +30% slope → prob_slope = 1.0
    }
    prob = get_recession_probability(macro)
    # 0.65 * 0 + 0.35 * 1.0 = 0.35
    assert prob == pytest.approx(0.35, abs=0.05)
