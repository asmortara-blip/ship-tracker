"""Tests for routes.route_registry and routes.rate_estimator.

ROUTE_REGISTRY coverage
-----------------------
  - ShippingRoute dataclass shape: frozen, exact field set.
  - ROUTES catalog invariants:
      * Non-empty, all entries are ShippingRoute, IDs unique.
      * Every Route has all string fields populated and positive transit_days.
      * Locodes are 5-char uppercase alpha; origin != dest.
      * FBX codes are non-empty strings; every fbx_index appears in ROUTES_BY_FBX.
  - ROUTES_BY_ID: keys equal {r.id for r in ROUTES}; each value is identity-equal
    to the catalog entry; covers same length as ROUTES (no collisions).
  - ROUTES_BY_FBX: defined per-route; last-wins semantics for shared FBX codes
    is honored (i.e., multiple routes can share an FBX index; the dict picks one).
  - FRED_FREIGHT_PROXIES: every catalog ID present, every value non-empty string.
  - get_route: known id, unknown id (returns None), empty string.
  - get_route_by_fbx: known fbx, unknown fbx (returns None).
  - get_all_route_ids: order matches ROUTES list; same length.
  - get_all_fbx_indices: order matches ROUTES list; same length.

RATE_ESTIMATOR coverage
-----------------------
  - compute_rate_momentum branches:
      * route missing from dict -> 0.5
      * empty DataFrame -> 0.5
      * single row -> 0.5
      * all-NaN rates after dropna -> 0.5
      * rolling average exactly 0 -> 0.5
      * current == rolling_avg -> 0.5 (ratio 1.0 -> (1.0-0.5)/1.0 = 0.5)
      * current >> rolling_avg -> clamped to 1.0
      * current << rolling_avg -> clamped to 0.0
      * unsorted dates: function sorts by date before computing
      * lookback_days respected (tail(n))
      * KNOWN GAP: a non-empty df without the `rate_usd_per_feu` column
        raises KeyError (unlike compute_rate_pct_change which guards it).
        Captured as a regression-locking test below.
  - compute_rate_pct_change branches:
      * route missing -> 0.0
      * len < 2 -> 0.0
      * missing 'rate_usd_per_feu' column -> 0.0
      * start == 0 -> 0.0
      * normal positive return
      * normal negative return
      * `days` slicing: tail(days+1)
  - get_all_route_rates:
      * empty freight_data -> entry per route, all zeros, trend "Stable", momentum 0.5
      * populated for a subset of routes -> covered routes have real values,
        uncovered have zeros/defaults
      * DataFrame missing rate column -> falls through to current_rate = 0.0
      * Summary dict has every route id from ROUTES with the expected schema.

All seeds are explicit integers; floats compared with pytest.approx.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

import numpy as np
import pandas as pd
import pytest

from routes.rate_estimator import (
    compute_rate_momentum,
    compute_rate_pct_change,
    get_all_route_rates,
)
from routes.route_registry import (
    FRED_FREIGHT_PROXIES,
    ROUTES,
    ROUTES_BY_FBX,
    ROUTES_BY_ID,
    ShippingRoute,
    get_all_fbx_indices,
    get_all_route_ids,
    get_route,
    get_route_by_fbx,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_rate_df(
    rates: Iterable[float],
    *,
    start: date = date(2026, 1, 1),
    sorted_asc: bool = True,
    include_rate_col: bool = True,
) -> pd.DataFrame:
    """Build a synthetic freight-rate DataFrame.

    Always carries a `date` column. If `include_rate_col` is False, the
    `rate_usd_per_feu` column is omitted so the missing-column branch can
    be exercised.
    """
    rates = list(rates)
    dates = [start + timedelta(days=i) for i in range(len(rates))]
    data = {"date": pd.to_datetime(dates)}
    if include_rate_col:
        data["rate_usd_per_feu"] = rates
    df = pd.DataFrame(data)
    if not sorted_asc:
        df = df.iloc[::-1].reset_index(drop=True)
    return df


# ===========================================================================
# ============================ route_registry.py ============================
# ===========================================================================


class TestShippingRouteDataclass:
    def test_is_frozen(self) -> None:
        r = ROUTES[0]
        with pytest.raises(Exception):  # FrozenInstanceError subclass of Exception
            r.id = "mutated"  # type: ignore[misc]

    def test_field_set_is_exact(self) -> None:
        expected = {
            "id",
            "name",
            "origin_region",
            "dest_region",
            "origin_locode",
            "dest_locode",
            "transit_days",
            "fbx_index",
            "description",
        }
        actual = set(ROUTES[0].__dataclass_fields__.keys())
        assert actual == expected


class TestRoutesCatalog:
    def test_non_empty(self) -> None:
        assert len(ROUTES) > 0

    def test_all_entries_are_shipping_routes(self) -> None:
        assert all(isinstance(r, ShippingRoute) for r in ROUTES)

    def test_ids_unique(self) -> None:
        ids = [r.id for r in ROUTES]
        assert len(ids) == len(set(ids)), "Duplicate route IDs in ROUTES"

    def test_required_string_fields_populated(self) -> None:
        for r in ROUTES:
            for field in (
                "id",
                "name",
                "origin_region",
                "dest_region",
                "origin_locode",
                "dest_locode",
                "fbx_index",
                "description",
            ):
                val = getattr(r, field)
                assert isinstance(val, str) and val, f"{r.id}.{field} is empty/non-str"

    def test_transit_days_positive_int(self) -> None:
        for r in ROUTES:
            assert isinstance(r.transit_days, int)
            assert r.transit_days > 0, f"{r.id} has non-positive transit_days"

    def test_locodes_well_formed(self) -> None:
        # UN/LOCODE: 5 chars, country code + port code, uppercase alpha.
        for r in ROUTES:
            for locode in (r.origin_locode, r.dest_locode):
                assert len(locode) == 5, f"{r.id} locode {locode!r} not 5 chars"
                assert locode.isupper(), f"{r.id} locode {locode!r} not uppercase"
                assert locode.isalpha(), f"{r.id} locode {locode!r} non-alpha"

    def test_origin_differs_from_dest(self) -> None:
        for r in ROUTES:
            assert r.origin_locode != r.dest_locode, (
                f"{r.id} has identical origin/dest locode"
            )


class TestRoutesByIdMapping:
    def test_keys_match_catalog_ids(self) -> None:
        assert set(ROUTES_BY_ID.keys()) == {r.id for r in ROUTES}

    def test_values_identity_match(self) -> None:
        for r in ROUTES:
            assert ROUTES_BY_ID[r.id] is r

    def test_no_collisions(self) -> None:
        # If two routes shared an id, the dict would be shorter than the list.
        assert len(ROUTES_BY_ID) == len(ROUTES)


class TestRoutesByFbxMapping:
    def test_all_fbx_values_appear(self) -> None:
        fbx_codes = {r.fbx_index for r in ROUTES}
        assert set(ROUTES_BY_FBX.keys()) == fbx_codes

    def test_value_is_a_route_with_that_fbx(self) -> None:
        for fbx, route in ROUTES_BY_FBX.items():
            assert isinstance(route, ShippingRoute)
            assert route.fbx_index == fbx

    def test_length_equals_unique_fbx_count(self) -> None:
        # Several routes share FBX codes (e.g., multiple FBX03 lanes).
        # The dict collapses to one entry per FBX code.
        assert len(ROUTES_BY_FBX) == len({r.fbx_index for r in ROUTES})


class TestFredFreightProxies:
    def test_every_route_has_a_proxy(self) -> None:
        catalog_ids = {r.id for r in ROUTES}
        assert catalog_ids.issubset(set(FRED_FREIGHT_PROXIES.keys()))

    def test_all_values_non_empty_strings(self) -> None:
        for rid, series in FRED_FREIGHT_PROXIES.items():
            assert isinstance(series, str) and series, f"{rid} proxy is empty"


class TestRegistryHelpers:
    def test_get_route_known(self) -> None:
        r = get_route("transpacific_eb")
        assert r is not None
        assert r.id == "transpacific_eb"
        assert r.origin_locode == "CNSHA"

    def test_get_route_unknown_returns_none(self) -> None:
        assert get_route("nonexistent_route_xyz") is None

    def test_get_route_empty_string_returns_none(self) -> None:
        assert get_route("") is None

    def test_get_route_by_fbx_known(self) -> None:
        r = get_route_by_fbx("FBX01")
        assert r is not None
        assert r.fbx_index == "FBX01"

    def test_get_route_by_fbx_unknown_returns_none(self) -> None:
        assert get_route_by_fbx("FBX_DOES_NOT_EXIST") is None

    def test_get_all_route_ids_matches_catalog_order(self) -> None:
        assert get_all_route_ids() == [r.id for r in ROUTES]

    def test_get_all_fbx_indices_matches_catalog_order(self) -> None:
        # Note: not deduplicated — preserves per-route order.
        assert get_all_fbx_indices() == [r.fbx_index for r in ROUTES]
        assert len(get_all_fbx_indices()) == len(ROUTES)


# ===========================================================================
# ============================ rate_estimator.py ============================
# ===========================================================================


class TestComputeRateMomentum:
    def test_missing_route_returns_neutral(self) -> None:
        assert compute_rate_momentum("transpacific_eb", {}) == 0.5

    def test_empty_dataframe_returns_neutral(self) -> None:
        freight = {"transpacific_eb": pd.DataFrame()}
        assert compute_rate_momentum("transpacific_eb", freight) == 0.5

    def test_single_row_returns_neutral(self) -> None:
        freight = {"transpacific_eb": _make_rate_df([2000.0])}
        assert compute_rate_momentum("transpacific_eb", freight) == 0.5

    def test_all_nan_rates_returns_neutral(self) -> None:
        # len(df) >= 2 but dropna() leaves <2 rows.
        df = _make_rate_df([np.nan, np.nan, np.nan])
        assert compute_rate_momentum("transpacific_eb", {"transpacific_eb": df}) == 0.5

    def test_rolling_average_zero_returns_neutral(self) -> None:
        df = _make_rate_df([0.0, 0.0, 0.0, 0.0])
        assert compute_rate_momentum("transpacific_eb", {"transpacific_eb": df}) == 0.5

    def test_at_average_yields_half(self) -> None:
        # All rates equal -> ratio = 1.0 -> score = (1.0 - 0.5) / 1.0 = 0.5
        df = _make_rate_df([1000.0] * 10)
        score = compute_rate_momentum("transpacific_eb", {"transpacific_eb": df})
        assert score == pytest.approx(0.5)

    def test_strong_uptrend_clamped_to_one(self) -> None:
        # current 5000, rolling avg ~ 2500-ish across short series -> ratio > 1.5 -> clamp 1.0
        rates = [1000.0, 1000.0, 1000.0, 5000.0]
        df = _make_rate_df(rates)
        score = compute_rate_momentum("transpacific_eb", {"transpacific_eb": df})
        assert score == pytest.approx(1.0)

    def test_strong_downtrend_clamped_to_zero(self) -> None:
        # current rate way below rolling average -> ratio < 0.5 -> clamp 0.0
        rates = [10000.0, 10000.0, 10000.0, 100.0]
        df = _make_rate_df(rates)
        score = compute_rate_momentum("transpacific_eb", {"transpacific_eb": df})
        assert score == pytest.approx(0.0)

    def test_moderate_uptrend_interior_value(self) -> None:
        # current 2400, rolling avg = 2000 -> ratio = 1.2 -> score = 0.7
        rates = [2000.0, 2000.0, 2000.0, 2400.0]
        df = _make_rate_df(rates)
        score = compute_rate_momentum("transpacific_eb", {"transpacific_eb": df})
        # rolling avg = mean([2000, 2000, 2000, 2400]) = 2100; ratio = 2400/2100 = 1.142857...
        expected = (2400.0 / 2100.0 - 0.5) / 1.0
        assert score == pytest.approx(expected)

    def test_unsorted_dates_get_sorted(self) -> None:
        # Build descending then verify the function still pulls the
        # max-date rate as "current".
        rates = [2000.0, 2050.0, 2100.0, 2150.0]
        df_asc = _make_rate_df(rates, sorted_asc=True)
        df_desc = _make_rate_df(rates, sorted_asc=False)
        score_asc = compute_rate_momentum("transpacific_eb", {"transpacific_eb": df_asc})
        score_desc = compute_rate_momentum("transpacific_eb", {"transpacific_eb": df_desc})
        assert score_asc == pytest.approx(score_desc)

    def test_missing_rate_column_raises_keyerror(self) -> None:
        # Documents an asymmetry in the module: compute_rate_pct_change
        # guards the missing-column case, compute_rate_momentum does not.
        # If the source is fixed to short-circuit, replace with an
        # assertion that the function returns 0.5.
        df = _make_rate_df([1000.0, 1100.0, 1200.0], include_rate_col=False)
        with pytest.raises(KeyError):
            compute_rate_momentum("transpacific_eb", {"transpacific_eb": df})

    def test_lookback_days_respected(self) -> None:
        # 100 days at 1000, last 5 days at 2000. With lookback=5, rolling avg = 2000
        # (ratio=1) -> score 0.5. With lookback=100, rolling avg < 2000 -> score > 0.5.
        rates = [1000.0] * 95 + [2000.0] * 5
        df = _make_rate_df(rates)
        score_short = compute_rate_momentum(
            "transpacific_eb", {"transpacific_eb": df}, lookback_days=5
        )
        score_long = compute_rate_momentum(
            "transpacific_eb", {"transpacific_eb": df}, lookback_days=100
        )
        assert score_short == pytest.approx(0.5)
        assert score_long > 0.5


class TestComputeRatePctChange:
    def test_missing_route_returns_zero(self) -> None:
        assert compute_rate_pct_change("transpacific_eb", {}, days=30) == 0.0

    def test_single_row_returns_zero(self) -> None:
        df = _make_rate_df([1500.0])
        assert compute_rate_pct_change("transpacific_eb", {"transpacific_eb": df}) == 0.0

    def test_missing_rate_column_returns_zero(self) -> None:
        df = _make_rate_df([1000.0, 1100.0, 1200.0], include_rate_col=False)
        assert compute_rate_pct_change("transpacific_eb", {"transpacific_eb": df}) == 0.0

    def test_start_zero_returns_zero(self) -> None:
        # Start of the tail window is 0 -> short-circuit to 0.0
        df = _make_rate_df([0.0, 1000.0, 1100.0])
        assert (
            compute_rate_pct_change("transpacific_eb", {"transpacific_eb": df}, days=2)
            == 0.0
        )

    def test_positive_pct_change(self) -> None:
        # 31 points: start 1000, end 1200 -> +20%
        rates = [1000.0 + (200.0 / 30.0) * i for i in range(31)]
        df = _make_rate_df(rates)
        pct = compute_rate_pct_change(
            "transpacific_eb", {"transpacific_eb": df}, days=30
        )
        assert pct == pytest.approx(0.20)

    def test_negative_pct_change(self) -> None:
        # Start 2000, end 1500 over 31 pts -> -25%
        rates = [2000.0 - (500.0 / 30.0) * i for i in range(31)]
        df = _make_rate_df(rates)
        pct = compute_rate_pct_change(
            "transpacific_eb", {"transpacific_eb": df}, days=30
        )
        assert pct == pytest.approx(-0.25)

    def test_days_window_is_tail_days_plus_one(self) -> None:
        # 100-row series with linear ramp; days=10 means the last 11 points.
        rates = [100.0 + i for i in range(100)]
        df = _make_rate_df(rates)
        pct = compute_rate_pct_change(
            "transpacific_eb", {"transpacific_eb": df}, days=10
        )
        # tail(11): rates[89..99] -> start=189, end=199 -> 10/189
        assert pct == pytest.approx(10.0 / 189.0)

    def test_unsorted_dates_still_correct(self) -> None:
        # Function sorts by date internally.
        rates = [1000.0 + (100.0 / 30.0) * i for i in range(31)]
        df_desc = _make_rate_df(rates, sorted_asc=False)
        pct = compute_rate_pct_change(
            "transpacific_eb", {"transpacific_eb": df_desc}, days=30
        )
        assert pct == pytest.approx(0.10)


class TestGetAllRouteRates:
    def test_empty_freight_data_returns_defaults_for_all_routes(self) -> None:
        summary = get_all_route_rates({})
        assert set(summary.keys()) == {r.id for r in ROUTES}
        for rid, entry in summary.items():
            assert entry["current_rate"] == 0.0
            assert entry["pct_30d"] == 0.0
            assert entry["trend"] == "Stable"
            assert entry["momentum_score"] == 0.5

    def test_schema_keys_per_entry(self) -> None:
        summary = get_all_route_rates({})
        expected_keys = {"current_rate", "pct_30d", "trend", "momentum_score"}
        for entry in summary.values():
            assert set(entry.keys()) == expected_keys

    def test_populated_route_has_real_values(self) -> None:
        # 31-day ramp from 2000 -> 2200 (+10%).
        rates = [2000.0 + (200.0 / 30.0) * i for i in range(31)]
        df = _make_rate_df(rates)
        summary = get_all_route_rates({"transpacific_eb": df})
        eb = summary["transpacific_eb"]
        assert eb["current_rate"] == pytest.approx(2200.0)
        assert eb["pct_30d"] == pytest.approx(0.10)
        # +10% over the threshold (default 0.05) -> Rising
        assert eb["trend"] == "Rising"
        # rolling avg over the ramp is the mean ~ 2100; ratio = 2200/2100 = 1.0476
        # score = (1.0476 - 0.5) / 1.0 ~ 0.5476 (between 0 and 1)
        assert 0.5 < eb["momentum_score"] < 1.0

    def test_route_missing_rate_column_propagates_crash(self) -> None:
        # current_rate is guarded (line 77 column check), pct_30d is guarded
        # inside compute_rate_pct_change — but compute_rate_momentum is NOT,
        # so a populated-but-column-less DataFrame propagates the KeyError.
        # Locks in the same bug surfaced by TestComputeRateMomentum.
        df = _make_rate_df([1000.0, 1100.0], include_rate_col=False)
        with pytest.raises(KeyError):
            get_all_route_rates({"transpacific_eb": df})

    def test_empty_dataframe_falls_through_to_zero(self) -> None:
        summary = get_all_route_rates({"transpacific_eb": pd.DataFrame()})
        eb = summary["transpacific_eb"]
        assert eb["current_rate"] == 0.0
        assert eb["pct_30d"] == 0.0
        assert eb["momentum_score"] == 0.5

    def test_subset_population_leaves_others_default(self) -> None:
        rates = [2000.0] * 31
        df = _make_rate_df(rates)
        summary = get_all_route_rates({"transpacific_eb": df})
        # The populated route shows its real (flat) rate.
        assert summary["transpacific_eb"]["current_rate"] == pytest.approx(2000.0)
        # Every other route is at defaults.
        for rid, entry in summary.items():
            if rid == "transpacific_eb":
                continue
            assert entry["current_rate"] == 0.0
            assert entry["momentum_score"] == 0.5
            assert entry["trend"] == "Stable"
