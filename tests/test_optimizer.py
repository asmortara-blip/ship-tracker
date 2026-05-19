"""Pure-function tests for routes.optimizer.

The route optimizer scores every registry lane for booking opportunity by
blending four weighted components — rate momentum, demand imbalance,
congestion clearance and a shared macro tailwind — into a [0, 1] composite.
These tests pin:

  * the RouteOpportunity dataclass shape and one-row-per-registry-route output;
  * the default weight set (sums to 1.0) and the documented composite formula;
  * the [0, 1] clamp on opportunity_score and each component;
  * descending-score sort order;
  * directional invariants — low origin congestion lifts the score, strong
    destination demand lifts it, an oil-price headwind drags the macro term;
  * graceful degradation — empty port_results / freight_data default cleanly
    to a neutral 0.5 score;
  * determinism — the optimizer has no randomness.

Port inputs are real ``PortDemandResult`` instances (the optimizer matches on
``locode`` and reads ``demand_score`` / ``congestion_index``). Freight and
macro inputs are synthetic FRED/FBX-shaped DataFrames. No Streamlit, no feed.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from ports.demand_analyzer import PortDemandResult
from routes.optimizer import RouteOpportunity, optimize_all_routes
from routes.route_registry import ROUTES

_DEFAULT_WEIGHTS = {
    "rate_momentum": 0.35,
    "demand_imbalance": 0.30,
    "congestion_clearance": 0.20,
    "macro_tailwind": 0.15,
}


# ── Builders ────────────────────────────────────────────────────────────────


def _port(locode: str, demand: float, congestion: float) -> PortDemandResult:
    """A PortDemandResult carrying only the fields the optimizer reads."""
    return PortDemandResult(
        locode=locode,
        port_name=locode,
        region="TEST",
        country_iso3="XXX",
        demand_score=demand,
        demand_label="Moderate",
        demand_trend="Stable",
        import_value_usd=0.0,
        export_value_usd=0.0,
        top_products=[],
        congestion_index=congestion,
        vessel_count=0,
        throughput_teu_m=0.0,
        trade_flow_component=0.5,
        congestion_component=congestion,
        throughput_component=0.5,
        data_freshness="",
        has_real_data=True,
    )


def _freight_for_all_routes(rate: float = 2500.0, slope: float = 0.0) -> dict:
    """A flat (or trending) freight series for every registry route."""
    n = 100
    base = date.today() - timedelta(days=n)
    dates = pd.to_datetime([base + timedelta(days=i) for i in range(n)])
    out: dict[str, pd.DataFrame] = {}
    for route in ROUTES:
        out[route.id] = pd.DataFrame({
            "date": dates,
            "rate_usd_per_feu": [rate + slope * i for i in range(n)],
            "source": "fixture",
        })
    return out


def _macro_frame(series_id: str, values: list[float]) -> pd.DataFrame:
    n = len(values)
    base = date.today() - timedelta(days=n)
    dates = pd.to_datetime([base + timedelta(days=i) for i in range(n)])
    return pd.DataFrame({"date": dates, "value": values})


# ── Output schema & coverage ────────────────────────────────────────────────


def test_optimize_returns_one_opportunity_per_registry_route() -> None:
    results = optimize_all_routes([], {}, {})
    assert len(results) == len(ROUTES)
    assert all(isinstance(r, RouteOpportunity) for r in results)
    assert {r.route_id for r in results} == {route.id for route in ROUTES}


def test_route_opportunity_field_presence() -> None:
    result = optimize_all_routes([], {}, {})[0]
    assert isinstance(result, RouteOpportunity)
    for name in (
        "route_id", "route_name", "origin_region", "dest_region",
        "origin_locode", "dest_locode", "fbx_index", "rationale", "generated_at",
    ):
        assert isinstance(getattr(result, name), str), name
    assert isinstance(result.transit_days, int)
    for name in (
        "opportunity_score", "current_rate_usd_feu", "rate_pct_change_30d",
        "demand_imbalance", "origin_congestion", "dest_demand_score",
        "rate_momentum_component", "demand_imbalance_component",
        "congestion_clearance_component", "macro_tailwind_component",
    ):
        assert isinstance(getattr(result, name), float), name
    assert result.rate_trend in {"Rising", "Stable", "Falling"}
    assert result.rationale  # non-empty human-readable summary


# ── Weight set ──────────────────────────────────────────────────────────────


def test_default_component_weights_sum_to_one() -> None:
    """The four default opportunity weights form a convex combination."""
    assert sum(_DEFAULT_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)


# ── Score bounds & component bounds ─────────────────────────────────────────


def test_opportunity_score_within_unit_interval() -> None:
    """Every route's composite opportunity score stays within [0, 1]."""
    ports = [_port("CNSHA", 0.9, 0.9), _port("USLAX", 0.1, 0.1)]
    results = optimize_all_routes(ports, _freight_for_all_routes(slope=12.0), {})
    for r in results:
        assert 0.0 <= r.opportunity_score <= 1.0, r.route_id


def test_components_within_unit_interval() -> None:
    """Each of the four scored components is itself bounded to [0, 1]."""
    ports = [_port("CNSHA", 0.8, 0.7), _port("USLAX", 0.3, 0.2)]
    results = optimize_all_routes(ports, _freight_for_all_routes(slope=8.0), {})
    for r in results:
        for comp in (
            r.rate_momentum_component, r.demand_imbalance_component,
            r.congestion_clearance_component, r.macro_tailwind_component,
        ):
            assert 0.0 <= comp <= 1.0, r.route_id


def test_demand_imbalance_within_signed_unit_range() -> None:
    """demand_imbalance is dest_demand - origin_demand, so it lives in [-1, 1]."""
    ports = [_port("CNSHA", 1.0, 0.5), _port("USLAX", 0.0, 0.5)]
    results = optimize_all_routes(ports, {}, {})
    for r in results:
        assert -1.0 <= r.demand_imbalance <= 1.0, r.route_id


# ── Documented composite formula ────────────────────────────────────────────


def test_composite_score_matches_weighted_component_sum() -> None:
    """opportunity_score == Σ weight_i * component_i, clamped to [0, 1]."""
    ports = [_port("CNSHA", 0.7, 0.6), _port("USLAX", 0.4, 0.3)]
    results = optimize_all_routes(ports, _freight_for_all_routes(slope=5.0), {})
    for r in results:
        recombined = (
            _DEFAULT_WEIGHTS["rate_momentum"] * r.rate_momentum_component
            + _DEFAULT_WEIGHTS["demand_imbalance"] * r.demand_imbalance_component
            + _DEFAULT_WEIGHTS["congestion_clearance"] * r.congestion_clearance_component
            + _DEFAULT_WEIGHTS["macro_tailwind"] * r.macro_tailwind_component
        )
        expected = max(0.0, min(1.0, recombined))
        assert r.opportunity_score == pytest.approx(expected, abs=1e-9), r.route_id


def test_congestion_clearance_is_complement_of_origin_congestion() -> None:
    """congestion_clearance_component == 1 - origin_congestion."""
    ports = [_port("CNSHA", 0.5, 0.72), _port("USLAX", 0.5, 0.5)]
    results = optimize_all_routes(ports, {}, {})
    tpeb = next(r for r in results if r.route_id == "transpacific_eb")
    assert tpeb.origin_congestion == pytest.approx(0.72, abs=1e-9)
    assert tpeb.congestion_clearance_component == pytest.approx(1.0 - 0.72, abs=1e-9)


# ── Sort order ──────────────────────────────────────────────────────────────


def test_results_sorted_by_opportunity_score_descending() -> None:
    ports = [_port("CNSHA", 0.8, 0.6), _port("USLAX", 0.3, 0.2)]
    results = optimize_all_routes(ports, _freight_for_all_routes(slope=6.0), {})
    scores = [r.opportunity_score for r in results]
    assert scores == sorted(scores, reverse=True)


# ── Directional invariants ──────────────────────────────────────────────────


def test_lower_origin_congestion_raises_opportunity() -> None:
    """Easier loading at the origin port lifts the route's opportunity score.

    transpacific_eb originates at CNSHA; only origin congestion is varied.
    """
    calm = [_port("CNSHA", 0.5, 0.10), _port("USLAX", 0.5, 0.5)]
    jammed = [_port("CNSHA", 0.5, 0.90), _port("USLAX", 0.5, 0.5)]
    calm_tpeb = next(
        r for r in optimize_all_routes(calm, {}, {}) if r.route_id == "transpacific_eb"
    )
    jam_tpeb = next(
        r for r in optimize_all_routes(jammed, {}, {}) if r.route_id == "transpacific_eb"
    )
    assert calm_tpeb.opportunity_score > jam_tpeb.opportunity_score


def test_stronger_destination_demand_raises_opportunity() -> None:
    """A demand surplus at the destination lifts the route's opportunity score.

    transpacific_eb terminates at USLAX; origin demand/congestion held flat.
    """
    weak_dest = [_port("CNSHA", 0.5, 0.5), _port("USLAX", 0.10, 0.5)]
    strong_dest = [_port("CNSHA", 0.5, 0.5), _port("USLAX", 0.90, 0.5)]
    weak = next(
        r for r in optimize_all_routes(weak_dest, {}, {}) if r.route_id == "transpacific_eb"
    )
    strong = next(
        r for r in optimize_all_routes(strong_dest, {}, {}) if r.route_id == "transpacific_eb"
    )
    assert strong.demand_imbalance > weak.demand_imbalance
    assert strong.opportunity_score > weak.opportunity_score


def test_high_oil_price_drags_macro_tailwind_versus_low_oil() -> None:
    """A WTI spike feeds the fuel-inverse term down, lowering macro tailwind.

    All other inputs are held identical, so the macro term is the only mover.
    """
    cheap_oil = {"DCOILWTICO": _macro_frame("DCOILWTICO", [45.0] * 30)}
    dear_oil = {"DCOILWTICO": _macro_frame("DCOILWTICO", [115.0] * 30)}
    cheap = optimize_all_routes([], {}, cheap_oil)[0]
    dear = optimize_all_routes([], {}, dear_oil)[0]
    assert cheap.macro_tailwind_component > dear.macro_tailwind_component


# ── Graceful degradation ────────────────────────────────────────────────────


def test_empty_inputs_yield_neutral_half_score() -> None:
    """No ports, no freight, no macro → every component defaults to 0.5."""
    results = optimize_all_routes([], {}, {})
    for r in results:
        assert r.opportunity_score == pytest.approx(0.5, abs=1e-9), r.route_id
        assert r.rate_momentum_component == pytest.approx(0.5, abs=1e-9)
        assert r.demand_imbalance_component == pytest.approx(0.5, abs=1e-9)
        assert r.congestion_clearance_component == pytest.approx(0.5, abs=1e-9)
        assert r.macro_tailwind_component == pytest.approx(0.5, abs=1e-9)
        assert r.current_rate_usd_feu == 0.0


def test_missing_port_results_fall_back_to_neutral_demand() -> None:
    """When a route's ports are absent, demand defaults to a balanced 0.5/0.5."""
    results = optimize_all_routes([], _freight_for_all_routes(), {})
    for r in results:
        assert r.dest_demand_score == pytest.approx(0.5, abs=1e-9), r.route_id
        assert r.demand_imbalance == pytest.approx(0.0, abs=1e-9), r.route_id
        assert r.origin_congestion == pytest.approx(0.5, abs=1e-9), r.route_id


def test_empty_freight_dataframe_does_not_crash() -> None:
    """A route mapped to an empty DataFrame yields a zero rate, neutral momentum."""
    empty = {route.id: pd.DataFrame() for route in ROUTES}
    results = optimize_all_routes([], empty, {})
    for r in results:
        assert r.current_rate_usd_feu == 0.0, r.route_id
        assert r.rate_momentum_component == pytest.approx(0.5, abs=1e-9), r.route_id


# ── Determinism ─────────────────────────────────────────────────────────────


def test_optimizer_is_deterministic() -> None:
    """The optimizer has no randomness — identical inputs give identical scores."""
    ports = [_port("CNSHA", 0.7, 0.4), _port("USLAX", 0.5, 0.3)]
    freight = _freight_for_all_routes(slope=4.0)
    a = optimize_all_routes(ports, freight, {})
    b = optimize_all_routes(ports, freight, {})
    assert {r.route_id: r.opportunity_score for r in a} == \
        {r.route_id: r.opportunity_score for r in b}
