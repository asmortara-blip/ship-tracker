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


# ── Per-driver conviction weight sets ───────────────────────────────────────


def test_every_conviction_weight_set_sums_to_one() -> None:
    """Each documented per-driver conviction weight set sums to exactly 1.0."""
    from processing.disruption_cascade import _CONVICTION_WEIGHT_SETS

    assert _CONVICTION_WEIGHT_SETS  # the table is non-empty
    for name, weight_set in _CONVICTION_WEIGHT_SETS.items():
        assert abs(sum(weight_set.values()) - 1.0) < 1e-9, name
        # Every set spans exactly the four named conviction terms.
        assert set(weight_set) == {
            "cascade", "agreement", "etf_confirm", "vulnerability"
        }, name


def test_chosen_weight_set_is_named_in_supporting_signals(
    ideas: list[EquityIdea],
) -> None:
    """The conviction weight set used is published on the idea and surfaced."""
    from processing.disruption_cascade import _CONVICTION_WEIGHT_SETS

    for idea in ideas:
        assert idea.conviction_weight_set in _CONVICTION_WEIGHT_SETS, idea.ticker
        # The set name appears verbatim in the decomposed supporting_signals.
        joined = " ".join(idea.supporting_signals)
        assert f"'{idea.conviction_weight_set}'" in joined, idea.ticker


def test_driver_specific_weight_set_is_selected() -> None:
    """A chokepoint-driven idea selects the cascade-heavy chokepoint set."""
    from processing.disruption_cascade import (
        _CONVICTION_WEIGHT_SETS,
        _conviction_weights_for,
    )

    # A rate-driven idea up-weights agreement vs. the chokepoint set, which
    # up-weights cascade — the published, documented per-driver distinction.
    chokepoint_name, chokepoint_w = _conviction_weights_for("chokepoint")
    rate_name, rate_w = _conviction_weights_for("rate")
    assert chokepoint_name == "chokepoint"
    assert rate_name == "rate"
    # Chokepoint ideas trust the physical cascade more; rate ideas trust
    # independent signal agreement more.
    assert chokepoint_w["cascade"] > rate_w["cascade"]
    assert rate_w["agreement"] > chokepoint_w["agreement"]
    # An unmapped / empty driver falls back to the balanced "default" set.
    default_name, default_w = _conviction_weights_for("")
    assert default_name == "default"
    assert default_w is _CONVICTION_WEIGHT_SETS["default"]


# ── Real per-route cargo shares ──────────────────────────────────────────────


def test_real_cargo_shares_used_when_mix_available(
    ideas: list[EquityIdea],
) -> None:
    """With cargo-mix data available, links use the real per-route share.

    The real per-route mix gives different routes different shares of the same
    commodity; the old uniform 1/N split made every route of a commodity carry
    an identical share. At least one idea must therefore show two links on the
    same commodity with *different* cargo shares.
    """
    saw_varying_share = False
    for idea in ideas:
        by_commodity: dict[str, set[float]] = {}
        for link in idea.cascade_chain:
            by_commodity.setdefault(link.hs_category, set()).add(link.cargo_share)
        if any(len(shares) > 1 for shares in by_commodity.values()):
            saw_varying_share = True
            break
    assert saw_varying_share, (
        "no idea showed route-varying cargo shares — real per-route cargo "
        "mix does not appear to be in use"
    )


def test_cargo_mix_source_is_surfaced(ideas: list[EquityIdea]) -> None:
    """Every idea with a cascade states which cargo-share source it used."""
    for idea in ideas:
        if not idea.cascade_chain:
            continue
        joined = " ".join(idea.supporting_signals)
        assert (
            "actual per-route cargo mix" in joined
            or "uniform 1/N cargo-share fallback" in joined
        ), idea.ticker


def test_route_cargo_shares_falls_back_to_uniform(monkeypatch) -> None:
    """When the real cargo mix is unavailable the uniform 1/N split is used."""
    from processing import disruption_cascade as dc

    # Force every get_route_cargo_mix call to yield no usable data.
    monkeypatch.setattr(
        dc.cargo_analyzer, "get_route_cargo_mix", lambda route_id, td: {}
    )
    routes = ["r1", "r2", "r3", "r4"]
    shares, used_real = dc._route_cargo_shares("electronics", routes, 0.40)
    assert used_real is False
    # Uniform split: exposure_weight / N on every route.
    for rid in routes:
        assert shares[rid] == pytest.approx(0.40 / 4, abs=1e-9)


def test_real_cargo_mix_is_preferred_over_uniform() -> None:
    """With real mix data, _route_cargo_shares reports the real-mix path."""
    from processing import disruption_cascade as dc

    shares, used_real = dc._route_cargo_shares(
        "electronics", ["transpacific_eb", "asia_europe"], 0.30
    )
    assert used_real is True
    # Real route mix: transpacific_eb carries more electronics than asia_europe.
    assert shares["transpacific_eb"] != shares["asia_europe"]
    assert all(0.0 <= v <= 1.0 for v in shares.values())


# ── Mean-reversion-aware vulnerability term ──────────────────────────────────


def test_persistence_factors_are_documented_and_bounded() -> None:
    """Per-driver persistence factors are an explicit table in (0, 1]."""
    from processing.disruption_cascade import (
        _DEFAULT_PERSISTENCE,
        _DRIVER_PERSISTENCE,
    )

    assert _DRIVER_PERSISTENCE  # the table is non-empty
    for driver, factor in _DRIVER_PERSISTENCE.items():
        assert 0.0 < factor <= 1.0, driver
    assert 0.0 < _DEFAULT_PERSISTENCE <= 1.0
    # Fast-reverting weather earns less than a slow-reverting chokepoint.
    assert _DRIVER_PERSISTENCE["weather"] < _DRIVER_PERSISTENCE["chokepoint"]
    assert _DRIVER_PERSISTENCE["weather"] < _DRIVER_PERSISTENCE["congestion"]


def test_cascade_link_contributions_decompose_exactly(
    ideas: list[EquityIdea],
) -> None:
    """Every link's contribution == route_stress x cargo_share x exposure_weight."""
    from processing.exposure_matrix import company_commodity_weights

    for idea in ideas:
        weights = company_commodity_weights(idea.ticker)
        for link in idea.cascade_chain:
            exposure_weight = weights.get(link.hs_category, 0.0)
            expected = link.route_stress * link.cargo_share * exposure_weight
            # Both factors and the product are rounded for display; a small
            # tolerance absorbs that rounding without hiding a real error.
            assert abs(expected - link.contribution) < 1e-3, (
                f"{idea.ticker}:{link.route_id}:{link.hs_category}"
            )
