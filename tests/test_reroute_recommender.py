"""Pure-function tests for processing.reroute_recommender (R022).

The reroute recommender proposes COSTED SUBSTITUTE corridors when a lane or a
maritime chokepoint is stressed. Given a stressed descriptor plus REAL
congestion forecasts and port supply states, it ranks alternative routes by a
documented composite of four sub-scores:

  * congestion headroom   (its forecast 30-day congestion vs. the stressed lane)
  * transit-day delta     (extra sea-days = a cost)
  * supply-deficit headroom (container-short ports = a cost)
  * $/FEU rate delta      (the money cost of the detour)

These tests pin:
  * the RerouteOption shape + the four documented weights (sum to 1.0);
  * candidate selection — a like-for-like lane swap (same origin+dest region)
    for a stressed LANE; a same-destination-market bypass for a CHOKEPOINT;
  * the stressed lane itself is always excluded; for a chokepoint, every option
    genuinely bypasses it (no candidate that also transits it);
  * ranking — a high-headroom / low-extra-transit substitute outranks a
    congested far detour;
  * exact transit-day delta + $/FEU delta arithmetic;
  * honest empty state (no similar corridor → []);
  * never raises on garbage / empty input;
  * determinism.

Inputs are hand-built lightweight route stubs + plain-dict congestion/supply so
the test pins the ranker's own logic, not the upstream feeds. A couple of cases
exercise the REAL registry + chokepoint linkage to confirm the wiring.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from processing.reroute_recommender import (
    CONGESTION_HEADROOM_WEIGHT,
    RATE_DELTA_WEIGHT,
    REROUTE_WEIGHTS,
    RerouteOption,
    SUPPLY_HEADROOM_WEIGHT,
    TRANSIT_DELTA_WEIGHT,
    recommend_reroutes,
)


# ── Lightweight stubs ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Route:
    id: str
    name: str
    origin_region: str
    dest_region: str
    origin_locode: str
    dest_locode: str
    transit_days: int


@dataclass
class _Cong:
    """Stand-in for CongestionForecast — only band_30d is read."""
    band_30d: tuple


@dataclass
class _Supply:
    supply_deficit_days: float


def _cong(high: float) -> _Cong:
    """A congestion forecast whose 30-day high edge is `high`."""
    return _Cong(band_30d=(max(0.0, high - 0.1), high))


# A small synthetic Asia→Europe market: one stressed lane + several substitutes
# with deliberately different headroom / transit / supply / rate profiles.
_STRESSED = _Route(
    "lane_stressed", "Stressed Lane", "Asia East", "Europe",
    "CNSHA", "NLRTM", 25,
)
_GOOD = _Route(   # lots of headroom, barely longer — should rank #1
    "lane_good", "Good Substitute", "Asia East", "Europe",
    "CNNBO", "BEANR", 26,
)
_FAR = _Route(    # mild headroom but a far, expensive detour — should rank low
    "lane_far", "Far Detour", "Asia East", "Europe",
    "LKCMB", "GBFXT", 38,
)
_OTHER_MARKET = _Route(  # different dest region — never a candidate
    "lane_other", "Different Market", "Asia East", "North America West",
    "CNSHA", "USLAX", 14,
)

_ROUTES = [_STRESSED, _GOOD, _FAR, _OTHER_MARKET]

_CONGESTION = {
    "NLRTM": _cong(0.85),   # stressed dest — very congested (the baseline)
    "BEANR": _cong(0.40),   # good substitute — lots of headroom
    "GBFXT": _cong(0.70),   # far detour — only mild headroom
    "USLAX": _cong(0.30),
}

_SUPPLY = {
    "CNSHA": _Supply(0.0), "NLRTM": _Supply(-1.0),
    "CNNBO": _Supply(2.0), "BEANR": _Supply(0.0),    # good — no deficit
    "LKCMB": _Supply(-9.0), "GBFXT": _Supply(-2.0),  # far — a short port
}

_RATES = {
    "lane_stressed": 4000.0,
    "lane_good": 4300.0,    # +$300 detour premium
    "lane_far": 6500.0,     # +$2500 — a heavy premium
}


# ── Shape & weights ─────────────────────────────────────────────────────────


def test_weights_sum_to_one() -> None:
    assert sum(REROUTE_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)
    assert (
        CONGESTION_HEADROOM_WEIGHT
        + TRANSIT_DELTA_WEIGHT
        + SUPPLY_HEADROOM_WEIGHT
        + RATE_DELTA_WEIGHT
    ) == pytest.approx(1.0, abs=1e-9)


def test_returns_reroute_options_with_full_shape() -> None:
    opts = recommend_reroutes(_STRESSED, _ROUTES, _CONGESTION, _SUPPLY, rates=_RATES)
    assert opts and all(isinstance(o, RerouteOption) for o in opts)
    o = opts[0]
    for name in ("substitute_route_id", "substitute_route_name", "rationale"):
        assert isinstance(getattr(o, name), str) and getattr(o, name)
    for name in (
        "congestion_headroom_score", "transit_delta_score",
        "supply_headroom_score", "rate_delta_score", "composite_score",
    ):
        assert 0.0 <= getattr(o, name) <= 1.0, name
    assert isinstance(o.extra_transit_days, int)
    assert isinstance(o.provenance, list)


# ── Ranking ─────────────────────────────────────────────────────────────────


def test_high_headroom_low_transit_substitute_ranks_first() -> None:
    """The roomy, barely-longer lane beats the congested far detour."""
    opts = recommend_reroutes(_STRESSED, _ROUTES, _CONGESTION, _SUPPLY, rates=_RATES)
    ids = [o.substitute_route_id for o in opts]
    assert ids[0] == "lane_good"
    assert ids.index("lane_good") < ids.index("lane_far")
    # And the composite ordering is monotone (it drives the rank).
    assert opts[0].composite_score >= opts[-1].composite_score


def test_more_headroom_raises_congestion_subscore() -> None:
    opts = recommend_reroutes(_STRESSED, _ROUTES, _CONGESTION, _SUPPLY, rates=_RATES)
    good = next(o for o in opts if o.substitute_route_id == "lane_good")
    far = next(o for o in opts if o.substitute_route_id == "lane_far")
    # Good has dest congestion 0.40 vs Far 0.70 against an 0.85 baseline.
    assert good.congestion_headroom > far.congestion_headroom
    assert good.congestion_headroom_score > far.congestion_headroom_score


# ── Exclusions ──────────────────────────────────────────────────────────────


def test_stressed_lane_is_excluded() -> None:
    opts = recommend_reroutes(_STRESSED, _ROUTES, _CONGESTION, _SUPPLY, rates=_RATES)
    assert "lane_stressed" not in {o.substitute_route_id for o in opts}


def test_different_market_route_is_not_a_candidate() -> None:
    opts = recommend_reroutes(_STRESSED, _ROUTES, _CONGESTION, _SUPPLY, rates=_RATES)
    assert "lane_other" not in {o.substitute_route_id for o in opts}


# ── Delta arithmetic ────────────────────────────────────────────────────────


def test_transit_day_delta_is_exact() -> None:
    opts = recommend_reroutes(_STRESSED, _ROUTES, _CONGESTION, _SUPPLY, rates=_RATES)
    good = next(o for o in opts if o.substitute_route_id == "lane_good")
    far = next(o for o in opts if o.substitute_route_id == "lane_far")
    assert good.extra_transit_days == 26 - 25       # +1
    assert far.extra_transit_days == 38 - 25        # +13


def test_rate_delta_is_exact() -> None:
    opts = recommend_reroutes(_STRESSED, _ROUTES, _CONGESTION, _SUPPLY, rates=_RATES)
    good = next(o for o in opts if o.substitute_route_id == "lane_good")
    far = next(o for o in opts if o.substitute_route_id == "lane_far")
    assert good.extra_cost_usd_feu == pytest.approx(4300.0 - 4000.0)   # +300
    assert far.extra_cost_usd_feu == pytest.approx(6500.0 - 4000.0)    # +2500


def test_composite_matches_weighted_subscore_sum() -> None:
    opts = recommend_reroutes(_STRESSED, _ROUTES, _CONGESTION, _SUPPLY, rates=_RATES)
    for o in opts:
        recombined = (
            CONGESTION_HEADROOM_WEIGHT * o.congestion_headroom_score
            + TRANSIT_DELTA_WEIGHT * o.transit_delta_score
            + SUPPLY_HEADROOM_WEIGHT * o.supply_headroom_score
            + RATE_DELTA_WEIGHT * o.rate_delta_score
        )
        assert o.composite_score == pytest.approx(
            max(0.0, min(1.0, recombined)), abs=1e-3
        ), o.substitute_route_id


# ── Empty / honest states ───────────────────────────────────────────────────


def test_no_similar_corridor_yields_empty_list() -> None:
    """A lane whose market no other route serves → no substitute, honest []."""
    lonely = _Route(
        "lonely", "Lonely Lane", "Antarctica", "Moon",
        "AAAAA", "ZZZZZ", 99,
    )
    routes = [lonely, _GOOD, _FAR]   # no other Antarctica→Moon route
    assert recommend_reroutes(lonely, routes, _CONGESTION, _SUPPLY) == []


def test_only_candidate_is_the_stressed_lane_yields_empty() -> None:
    assert recommend_reroutes(_STRESSED, [_STRESSED], _CONGESTION, _SUPPLY) == []


def test_top_n_caps_the_result() -> None:
    opts = recommend_reroutes(_STRESSED, _ROUTES, _CONGESTION, _SUPPLY, rates=_RATES, top_n=1)
    assert len(opts) == 1


# ── Never raises on garbage ─────────────────────────────────────────────────


def test_none_descriptor_returns_empty() -> None:
    assert recommend_reroutes(None, _ROUTES, _CONGESTION, _SUPPLY) == []


def test_empty_routes_returns_empty() -> None:
    assert recommend_reroutes(_STRESSED, [], {}, {}) == []


def test_unknown_chokepoint_returns_empty() -> None:
    assert recommend_reroutes("not_a_real_chokepoint", _ROUTES, {}, {}) == []


def test_garbage_inputs_never_raise() -> None:
    for bad_routes in (None, [None, None], [{"id": "x"}], ["junk"]):
        out = recommend_reroutes(_STRESSED, bad_routes, None, None)  # type: ignore[arg-type]
        assert isinstance(out, list)
    # Garbage congestion / supply shapes degrade, never raise.
    out = recommend_reroutes(
        _STRESSED, _ROUTES, {"NLRTM": "nonsense"}, {"CNNBO": object()}  # type: ignore[dict-item]
    )
    assert isinstance(out, list)


def test_missing_congestion_degrades_to_neutral_not_crash() -> None:
    """No forecast for a substitute → neutral congestion contribution, no crash."""
    opts = recommend_reroutes(_STRESSED, _ROUTES, {}, _SUPPLY, rates=_RATES)
    assert isinstance(opts, list)
    for o in opts:
        assert "congestion" not in o.provenance   # no real forecast supplied
        assert 0.0 <= o.congestion_headroom_score <= 1.0


# ── Determinism ─────────────────────────────────────────────────────────────


def test_deterministic() -> None:
    a = recommend_reroutes(_STRESSED, _ROUTES, _CONGESTION, _SUPPLY, rates=_RATES)
    b = recommend_reroutes(_STRESSED, _ROUTES, _CONGESTION, _SUPPLY, rates=_RATES)
    assert [(o.substitute_route_id, o.composite_score) for o in a] == \
        [(o.substitute_route_id, o.composite_score) for o in b]


# ── Real registry + chokepoint linkage (wiring smoke) ───────────────────────


def test_real_chokepoint_bypass_excludes_routes_through_it() -> None:
    """For a real chokepoint stress, every option genuinely bypasses it."""
    from processing.chokepoint_analyzer import CHOKEPOINTS
    from routes.route_registry import ROUTES as REAL_ROUTES

    # Hormuz has in-registry bypass corridors (Europe-market lanes not via Hormuz).
    opts = recommend_reroutes("hormuz", REAL_ROUTES, {}, {}, top_n=5)
    through_hormuz = set(CHOKEPOINTS["hormuz"].affected_routes)
    assert opts, "expected at least one Hormuz bypass in the real registry"
    for o in opts:
        assert o.substitute_route_id not in through_hormuz
        assert o.bypasses_chokepoint == "hormuz"


def test_real_lane_stress_finds_like_for_like_swap() -> None:
    """A stressed real Asia→Europe lane should surface another Asia→Europe lane."""
    from routes.route_registry import ROUTES as REAL_ROUTES, ROUTES_BY_ID

    asia_eu = ROUTES_BY_ID["asia_europe"]
    opts = recommend_reroutes(asia_eu, REAL_ROUTES, {}, {})
    ids = {o.substitute_route_id for o in opts}
    assert "asia_europe" not in ids
    # ningbo_europe is the other Asia East → Europe registry lane.
    assert "ningbo_europe" in ids
