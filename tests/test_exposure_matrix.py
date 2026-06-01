"""Pure-function tests for processing.exposure_matrix.

The exposure matrix is a *dense* company↔commodity linkage derived
deterministically at import. These tests pin the per-company weight-vector
sum-to-1.0 invariant, the [0, 1] bounds on every weight, the constant
shapes, graceful degradation of the ETF lookup on empty/missing data, and
the public helpers. No Streamlit, no I/O.
"""
from __future__ import annotations

import pytest

from processing import cargo_analyzer
from processing.exposure_matrix import (
    COMMODITY_ETF_MAP,
    COMPANY_COMMODITY_EXPOSURE,
    CommodityExposure,
    build_exposure_matrix,
    company_commodity_weights,
    routes_for_commodity,
)

_HS_CATEGORIES = set(cargo_analyzer.HS_CATEGORIES)


# ── Weight invariant: per-company vectors sum to ≈ 1.0 ──────────────────────


def test_company_exposure_is_non_empty() -> None:
    """The matrix is derived at import and must always be populated."""
    assert len(COMPANY_COMMODITY_EXPOSURE) > 0


def test_every_company_vector_sums_to_one() -> None:
    """Each tracked company's HS-category weight vector sums to ≈ 1.0."""
    for ticker, weights in COMPANY_COMMODITY_EXPOSURE.items():
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6), ticker


def test_every_company_weight_in_unit_interval() -> None:
    for ticker, weights in COMPANY_COMMODITY_EXPOSURE.items():
        for category, weight in weights.items():
            assert 0.0 <= weight <= 1.0, f"{ticker}:{category}"


def test_company_vectors_cover_all_hs_categories() -> None:
    """Every company's vector spans exactly the HS-category universe."""
    for ticker, weights in COMPANY_COMMODITY_EXPOSURE.items():
        assert set(weights) == _HS_CATEGORIES, ticker


# ── Constant shapes ─────────────────────────────────────────────────────────


def test_commodity_etf_map_keys_are_hs_categories() -> None:
    """Every key of COMMODITY_ETF_MAP is a real HS category."""
    assert set(COMMODITY_ETF_MAP).issubset(_HS_CATEGORIES)


def test_commodity_etf_map_values_are_non_empty_strings() -> None:
    assert all(
        isinstance(t, str) and t for t in COMMODITY_ETF_MAP.values()
    )


# ── company_commodity_weights ───────────────────────────────────────────────


def test_company_commodity_weights_known_ticker_sums_to_one() -> None:
    ticker = next(iter(COMPANY_COMMODITY_EXPOSURE))
    weights = company_commodity_weights(ticker)
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)
    assert set(weights) == _HS_CATEGORIES


def test_company_commodity_weights_returns_a_copy() -> None:
    """Mutating the returned dict must not corrupt the module constant."""
    ticker = next(iter(COMPANY_COMMODITY_EXPOSURE))
    weights = company_commodity_weights(ticker)
    weights["electronics"] = 999.0
    assert COMPANY_COMMODITY_EXPOSURE[ticker] != weights


def test_company_commodity_weights_unknown_ticker_even_split() -> None:
    """An unknown ticker returns a non-empty even split, not an empty dict."""
    weights = company_commodity_weights("NOT_A_TICKER")
    assert set(weights) == _HS_CATEGORIES
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)
    # Even split: every weight identical.
    assert len(set(round(w, 9) for w in weights.values())) == 1


# ── routes_for_commodity ────────────────────────────────────────────────────


def test_routes_for_commodity_returns_list_of_strings() -> None:
    for category in _HS_CATEGORIES:
        routes = routes_for_commodity(category)
        assert isinstance(routes, list)
        assert all(isinstance(r, str) for r in routes)


def test_routes_for_commodity_unknown_category_empty() -> None:
    assert routes_for_commodity("not_a_commodity") == []


def test_routes_for_commodity_excludes_zero_share_routes() -> None:
    """Every route returned genuinely carries a meaningful share of the cargo.

    ``routes_for_commodity`` cross-checks the coarse region heuristic against
    each route's real cargo mix, so a route is never listed for a commodity
    its actual mix shows it does not carry.
    """
    from processing.exposure_matrix import _ROUTE_COMMODITY_MIN_SHARE

    for category in _HS_CATEGORIES:
        for route_id in routes_for_commodity(category):
            mix = cargo_analyzer.get_route_cargo_mix(route_id, {})
            assert mix.get(category, 0.0) >= _ROUTE_COMMODITY_MIN_SHARE, (
                f"{category}:{route_id} listed but carries "
                f"{mix.get(category, 0.0):.3f} share"
            )


def test_every_commodity_keeps_at_least_one_route() -> None:
    """The real-mix cross-check must not strand any commodity with no routes."""
    for category in _HS_CATEGORIES:
        assert len(routes_for_commodity(category)) > 0, category


# ── build_exposure_matrix: shape & graceful degradation ─────────────────────


def test_build_exposure_matrix_one_per_category() -> None:
    """One CommodityExposure per HS category, with empty stock_data."""
    matrix = build_exposure_matrix({})
    assert len(matrix) == len(_HS_CATEGORIES)
    assert all(isinstance(ce, CommodityExposure) for ce in matrix)
    assert {ce.hs_category for ce in matrix} == _HS_CATEGORIES


def test_build_exposure_matrix_empty_input_neutral_directions() -> None:
    """With no ETF data every category degrades to Neutral / 0.0 change."""
    matrix = build_exposure_matrix({})
    for ce in matrix:
        assert ce.direction == "Neutral"
        assert ce.etf_price_change_30d == 0.0


def test_build_exposure_matrix_none_input_does_not_raise() -> None:
    matrix = build_exposure_matrix(None)  # type: ignore[arg-type]
    assert len(matrix) == len(_HS_CATEGORIES)


def test_build_exposure_matrix_garbage_stock_data_does_not_raise() -> None:
    """Malformed per-ticker frames degrade gracefully rather than raising."""
    junk = {"XRT": "not-a-frame", "XLI": None, "DBA": 123}
    matrix = build_exposure_matrix(junk)
    assert len(matrix) == len(_HS_CATEGORIES)
    for ce in matrix:
        assert ce.direction in {"Bullish", "Bearish", "Neutral"}


def test_exposure_fields_are_well_formed() -> None:
    matrix = build_exposure_matrix({})
    for ce in matrix:
        assert ce.direction in {"Bullish", "Bearish", "Neutral"}
        assert isinstance(ce.affected_routes, list)
        assert isinstance(ce.bullish_companies, list)
        assert isinstance(ce.bearish_companies, list)
        assert isinstance(ce.exposure_note, str) and ce.exposure_note
        # Neutral / no-data → both company lists empty.
        if ce.direction == "Neutral":
            assert ce.bullish_companies == []
            assert ce.bearish_companies == []


# ── Determinism ─────────────────────────────────────────────────────────────


def test_build_exposure_matrix_is_repeatable() -> None:
    a = build_exposure_matrix({})
    b = build_exposure_matrix({})
    assert [ce.hs_category for ce in a] == [ce.hs_category for ce in b]
    assert [ce.direction for ce in a] == [ce.direction for ce in b]
