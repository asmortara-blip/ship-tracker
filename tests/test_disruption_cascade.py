"""Pure-function tests for processing.disruption_cascade.

The cascade scorer is the "Conclude" stage of the Disruption Alpha chain:
it walks Shipping-Stress-Index routes through the company↔commodity
exposure matrix to produce ranked, traceable ``EquityIdea`` objects. These
tests pin the [0, 1] conviction bound, the conviction-weight invariant, the
descending rank order, graceful degradation on degenerate inputs, and the
summary shape. No Streamlit, no live feed.
"""
from __future__ import annotations

import pytest

from processing.disruption_cascade import (
    CascadeLink,
    EquityIdea,
    cascade_summary,
    score_equity_ideas,
)
from processing.exposure_matrix import (
    COMPANY_COMMODITY_EXPOSURE,
    build_exposure_matrix,
)
from processing.shipping_stress_index import compute_shipping_stress

_DIRECTIONS = {"Bullish", "Bearish", "Neutral"}
_LABELS = {"High", "Moderate", "Low", "Watch"}


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def ideas() -> list[EquityIdea]:
    """Equity ideas from a real SSI report + a real exposure matrix."""
    report = compute_shipping_stress({}, {}, [], [], voyage_fleet=None)
    matrix = build_exposure_matrix({})
    return score_equity_ideas(report, matrix, {})


# ── Weight invariant ────────────────────────────────────────────────────────


def test_conviction_weights_sum_to_one() -> None:
    """The four conviction terms' weights must sum to 1.0."""
    from processing.disruption_cascade import _CONVICTION_WEIGHTS

    assert abs(sum(_CONVICTION_WEIGHTS.values()) - 1.0) < 1e-9


# ── Shape / sanity ──────────────────────────────────────────────────────────


def test_score_returns_list_of_equity_ideas(ideas: list[EquityIdea]) -> None:
    assert isinstance(ideas, list)
    assert len(ideas) > 0
    assert all(isinstance(idea, EquityIdea) for idea in ideas)


def test_one_idea_per_tracked_company(ideas: list[EquityIdea]) -> None:
    """Every company in the exposure matrix yields exactly one idea."""
    assert sorted(i.ticker for i in ideas) == sorted(
        COMPANY_COMMODITY_EXPOSURE.keys()
    )


def test_ideas_ranked_by_conviction_descending(
    ideas: list[EquityIdea],
) -> None:
    scores = [idea.conviction_score for idea in ideas]
    assert scores == sorted(scores, reverse=True)


def test_idea_fields_are_well_formed(ideas: list[EquityIdea]) -> None:
    for idea in ideas:
        assert idea.direction in _DIRECTIONS
        assert idea.conviction_label in _LABELS
        assert isinstance(idea.thesis, str) and idea.thesis
        assert isinstance(idea.cascade_chain, list)
        assert all(isinstance(lk, CascadeLink) for lk in idea.cascade_chain)
        assert isinstance(idea.driving_routes, list)
        assert isinstance(idea.driving_commodities, list)
        assert isinstance(idea.supporting_signals, list) and idea.supporting_signals
        assert isinstance(idea.risk_flags, list)
        assert isinstance(idea.generated_at, str) and idea.generated_at


def test_cascade_link_contributions_well_formed(
    ideas: list[EquityIdea],
) -> None:
    """Every cascade hop has [0, 1] route_stress / cargo_share and a >0 contribution."""
    for idea in ideas:
        for link in idea.cascade_chain:
            assert 0.0 <= link.route_stress <= 1.0
            assert 0.0 <= link.cargo_share <= 1.0
            assert link.contribution > 0.0
            assert link.commodity_signal in _DIRECTIONS
        # Chain is sorted worst-first.
        contributions = [lk.contribution for lk in idea.cascade_chain]
        assert contributions == sorted(contributions, reverse=True)


# ── Bounds ──────────────────────────────────────────────────────────────────


def test_conviction_score_in_unit_interval(ideas: list[EquityIdea]) -> None:
    for idea in ideas:
        assert 0.0 <= idea.conviction_score <= 1.0, idea.ticker


def test_neutral_ideas_capped_low(ideas: list[EquityIdea]) -> None:
    """A Neutral idea is capped at a low conviction so it never outranks a call."""
    for idea in ideas:
        if idea.direction == "Neutral":
            assert idea.conviction_score <= 0.21, idea.ticker


# ── Graceful degradation ────────────────────────────────────────────────────


def test_none_stress_report_yields_neutral_ideas() -> None:
    """A ``None`` stress report → every idea Neutral, never a crash."""
    matrix = build_exposure_matrix({})
    result = score_equity_ideas(None, matrix, {})
    assert len(result) == len(COMPANY_COMMODITY_EXPOSURE)
    assert all(idea.direction == "Neutral" for idea in result)
    assert all(0.0 <= idea.conviction_score <= 1.0 for idea in result)


def test_all_degenerate_inputs_do_not_raise() -> None:
    """None stress report, None exposure matrix, None stock_data → valid list."""
    result = score_equity_ideas(None, None, None)
    assert isinstance(result, list)
    assert len(result) == len(COMPANY_COMMODITY_EXPOSURE)
    for idea in result:
        assert idea.direction in _DIRECTIONS
        assert 0.0 <= idea.conviction_score <= 1.0


def test_empty_exposure_matrix_does_not_raise() -> None:
    """An empty exposure matrix still produces one idea per tracked company."""
    report = compute_shipping_stress({}, {}, [], [], voyage_fleet=None)
    result = score_equity_ideas(report, [], {})
    assert len(result) == len(COMPANY_COMMODITY_EXPOSURE)
    assert all(0.0 <= idea.conviction_score <= 1.0 for idea in result)


def test_none_insights_disable_corroboration() -> None:
    """``insights=None`` is accepted and simply disables the cross-reference."""
    report = compute_shipping_stress({}, {}, [], [], voyage_fleet=None)
    matrix = build_exposure_matrix({})
    result = score_equity_ideas(report, matrix, {}, insights=None)
    assert len(result) > 0


# ── cascade_summary ─────────────────────────────────────────────────────────


def test_cascade_summary_empty_list_neutral_defaults() -> None:
    """An empty idea list yields a valid all-zero summary, never an error."""
    summary = cascade_summary([])
    expected_keys = {
        "total", "bullish_count", "bearish_count", "neutral_count",
        "net_signal", "top_idea", "top_ticker", "avg_conviction",
        "high_conviction_count",
    }
    assert set(summary.keys()) == expected_keys
    assert summary["total"] == 0
    assert summary["net_signal"] == "Neutral"
    assert summary["top_idea"] is None
    assert summary["avg_conviction"] == 0.0


def test_cascade_summary_counts_partition_the_list(
    ideas: list[EquityIdea],
) -> None:
    summary = cascade_summary(ideas)
    assert summary["total"] == len(ideas)
    assert (
        summary["bullish_count"]
        + summary["bearish_count"]
        + summary["neutral_count"]
        == summary["total"]
    )
    assert summary["net_signal"] in _DIRECTIONS


def test_cascade_summary_top_idea_is_highest_conviction(
    ideas: list[EquityIdea],
) -> None:
    summary = cascade_summary(ideas)
    assert isinstance(summary["top_idea"], EquityIdea)
    assert summary["top_idea"].conviction_score == max(
        i.conviction_score for i in ideas
    )
    assert summary["top_ticker"] == summary["top_idea"].ticker
    assert 0.0 <= summary["avg_conviction"] <= 1.0
    assert 0 <= summary["high_conviction_count"] <= summary["total"]


# ── Determinism ─────────────────────────────────────────────────────────────


def test_score_equity_ideas_is_repeatable() -> None:
    """Identical inputs yield an identical ranked idea list within a session."""
    report = compute_shipping_stress({}, {}, [], [], voyage_fleet=None)
    matrix = build_exposure_matrix({})
    a = score_equity_ideas(report, matrix, {})
    b = score_equity_ideas(report, matrix, {})
    assert [i.ticker for i in a] == [i.ticker for i in b]
    assert [i.conviction_score for i in a] == [i.conviction_score for i in b]
    assert [i.direction for i in a] == [i.direction for i in b]
