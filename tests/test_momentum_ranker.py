"""Tests for engine.momentum_ranker.

Covers:
  - MomentumRank dataclass shape
  - Pure helpers: _composite, _regime, _signal, _pct_change_from_df,
    _route_momentum, _port_momentum
  - rank_all_momentum end-to-end: sorting, per-category rank assignment,
    empty-input handling, mixed-entity portfolios
  - get_top_momentum: filtering by limit and entity_type
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd
import pytest

from engine.momentum_ranker import (
    MomentumRank,
    _composite,
    _pct_change_from_df,
    _port_momentum,
    _regime,
    _route_momentum,
    _signal,
    get_top_momentum,
    rank_all_momentum,
)


# ─── Lightweight duck-typed stand-ins for Route/Port objects ────────────────

@dataclass
class _FakeRoute:
    route_id: str
    route_name: str = "Some Route"
    rate_pct_change_7d: float = 0.0
    rate_pct_change_30d: float = 0.0
    rate_pct_change_90d: float = 0.0
    rate_momentum_component: float = 0.0


@dataclass
class _FakePort:
    locode: str
    port_name: str = "Some Port"
    demand_score_7d: float = 0.0
    demand_score_30d: float = 0.0
    demand_score_90d: float = 0.0
    demand_score: float = 0.5


def _stock_df(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": closes})


# ─── MomentumRank dataclass ─────────────────────────────────────────────────

def test_momentum_rank_dataclass_shape() -> None:
    r = MomentumRank(
        entity_id="transpacific_eb", entity_type="route",
        entity_name="Trans-Pacific Eastbound",
        momentum_7d=0.05, momentum_30d=0.08, momentum_90d=0.12,
        momentum_composite=0.09, rank_overall=1, rank_in_category=1,
        regime="ACCELERATING", signal="BUY",
    )
    assert r.entity_type == "route"
    assert r.rank_overall == 1


# ─── _composite ─────────────────────────────────────────────────────────────

def test_composite_weights_match_doc() -> None:
    """Per source: 0.2 * m7 + 0.4 * m30 + 0.4 * m90."""
    assert _composite(0.1, 0.2, 0.3) == pytest.approx(0.22, abs=1e-9)
    assert _composite(0.0, 0.0, 0.0) == 0.0
    assert _composite(1.0, 0.0, 0.0) == pytest.approx(0.20)
    assert _composite(0.0, 0.0, 1.0) == pytest.approx(0.40)


def test_composite_handles_negative() -> None:
    """Negative momentum composes negative."""
    assert _composite(-0.1, -0.2, -0.3) == pytest.approx(-0.22, abs=1e-9)


# ─── _regime ────────────────────────────────────────────────────────────────

def test_regime_accelerating_when_m7_gt_m30_gt_m90() -> None:
    # 7d > 30d > 90d ⇒ momentum building
    assert _regime(0.15, 0.10, 0.05) == "ACCELERATING"


def test_regime_decelerating_when_m7_lt_m30_lt_m90() -> None:
    # 7d < 30d < 90d ⇒ momentum fading
    assert _regime(0.05, 0.10, 0.15) == "DECELERATING"


def test_regime_sustained_when_all_close() -> None:
    # spread <= 0.05 across the three readings
    assert _regime(0.10, 0.12, 0.08) == "SUSTAINED"
    assert _regime(0.0, 0.0, 0.0) == "SUSTAINED"


def test_regime_reversing_when_mixed_and_not_sustained() -> None:
    # Anything else falls to REVERSING
    assert _regime(0.20, 0.05, 0.15) == "REVERSING"


# ─── _signal ────────────────────────────────────────────────────────────────

def test_signal_thresholds() -> None:
    # > 0.15  → STRONG_BUY
    # > 0.05  → BUY
    # > -0.05 → NEUTRAL
    # > -0.15 → SELL
    # else    → STRONG_SELL
    assert _signal(0.20) == "STRONG_BUY"
    assert _signal(0.10) == "BUY"
    assert _signal(0.00) == "NEUTRAL"
    assert _signal(-0.10) == "SELL"
    assert _signal(-0.30) == "STRONG_SELL"


def test_signal_at_exact_boundaries() -> None:
    # Boundaries are strictly > so a value AT the threshold falls to the
    # weaker tier.
    assert _signal(0.15) == "BUY"
    assert _signal(0.05) == "NEUTRAL"
    assert _signal(-0.05) == "SELL"
    assert _signal(-0.15) == "STRONG_SELL"


# ─── _pct_change_from_df ────────────────────────────────────────────────────

def test_pct_change_from_df_basic_uplift() -> None:
    """30-day window: 100 → 120 = +20%."""
    closes = [100.0] * 60 + [120.0]
    df = _stock_df(closes)
    out = _pct_change_from_df(df, days=30)
    assert out == pytest.approx(0.20, abs=1e-4)


def test_pct_change_from_df_negative_move() -> None:
    closes = [200.0] * 60 + [150.0]
    df = _stock_df(closes)
    out = _pct_change_from_df(df, days=30)
    assert out == pytest.approx(-0.25, abs=1e-4)


def test_pct_change_from_df_empty_or_missing_col_returns_zero() -> None:
    assert _pct_change_from_df(None, 30) == 0.0
    assert _pct_change_from_df(pd.DataFrame(), 30) == 0.0
    assert _pct_change_from_df(pd.DataFrame({"open": [1, 2, 3]}), 30) == 0.0


def test_pct_change_from_df_too_few_rows() -> None:
    assert _pct_change_from_df(_stock_df([100.0]), 30) == 0.0


def test_pct_change_from_df_zero_old_returns_zero() -> None:
    df = _stock_df([0.0] + [100.0] * 30)
    # old=0 → divide guard fires → 0.0
    assert _pct_change_from_df(df, 30) == 0.0


def test_pct_change_from_df_caps_n_to_available_rows() -> None:
    """Asking for days > available rows still works — uses the earliest row."""
    df = _stock_df([100.0, 110.0])
    out = _pct_change_from_df(df, days=30)
    assert out == pytest.approx(0.10, abs=1e-4)


# ─── _route_momentum / _port_momentum extraction ────────────────────────────

def test_route_momentum_reads_explicit_fields() -> None:
    route = _FakeRoute(
        route_id="r1",
        rate_pct_change_7d=0.05,
        rate_pct_change_30d=0.08,
        rate_pct_change_90d=0.12,
    )
    m7, m30, m90 = _route_momentum(route)
    assert m7 == 0.05 and m30 == 0.08 and m90 == 0.12


def test_route_momentum_handles_missing_fields_gracefully() -> None:
    """A Route object without the explicit pct_change fields uses 0.0
    or the rate_momentum_component fallback (impl-dependent), never raises."""
    route = _FakeRoute(route_id="r2")   # all fields default to 0.0
    m7, m30, m90 = _route_momentum(route)
    assert math.isfinite(m7)
    assert math.isfinite(m30)
    assert math.isfinite(m90)


def test_port_momentum_returns_finite_values() -> None:
    port = _FakePort(locode="USLAX", demand_score=0.6)
    m7, m30, m90 = _port_momentum(port)
    for m in (m7, m30, m90):
        assert math.isfinite(m)


# ─── rank_all_momentum end-to-end ───────────────────────────────────────────

def test_rank_all_momentum_empty_inputs() -> None:
    assert rank_all_momentum([], [], {}) == []


def test_rank_all_momentum_sorts_by_composite_desc() -> None:
    routes = [
        _FakeRoute(route_id="lo", rate_pct_change_7d=0.0, rate_pct_change_30d=0.0, rate_pct_change_90d=0.0),
        _FakeRoute(route_id="hi", rate_pct_change_7d=0.10, rate_pct_change_30d=0.15, rate_pct_change_90d=0.20),
        _FakeRoute(route_id="mid", rate_pct_change_7d=0.05, rate_pct_change_30d=0.05, rate_pct_change_90d=0.05),
    ]
    out = rank_all_momentum(routes, [], {})
    assert len(out) == 3
    # Sorted by momentum_composite desc.
    composites = [r.momentum_composite for r in out]
    assert composites == sorted(composites, reverse=True)
    # rank_overall == position + 1 in the sorted output
    for i, r in enumerate(out, start=1):
        assert r.rank_overall == i


def test_rank_all_momentum_assigns_per_category_rank() -> None:
    routes = [_FakeRoute(route_id="r1", rate_pct_change_30d=0.10),
              _FakeRoute(route_id="r2", rate_pct_change_30d=0.05)]
    ports = [_FakePort(locode="P1", demand_score=0.8),
             _FakePort(locode="P2", demand_score=0.6)]
    stocks = {"ZIM": _stock_df([15.0] * 60 + [18.0])}
    out = rank_all_momentum(routes, ports, stocks)
    # Group by entity_type; within each type the rank_in_category should be
    # strictly increasing starting at 1.
    by_type: dict[str, list[MomentumRank]] = {}
    for r in out:
        by_type.setdefault(r.entity_type, []).append(r)
    for type_records in by_type.values():
        type_records.sort(key=lambda r: r.rank_in_category)
        assert type_records[0].rank_in_category == 1
        for i in range(len(type_records) - 1):
            assert type_records[i].rank_in_category < type_records[i + 1].rank_in_category


def test_rank_all_momentum_handles_mixed_entities() -> None:
    """All three entity types in one call — every output has the expected
    type marker and a finite composite."""
    routes = [_FakeRoute(route_id="r", rate_pct_change_30d=0.10)]
    ports = [_FakePort(locode="P", demand_score=0.7)]
    stocks = {"ZIM": _stock_df([15.0] * 30 + [16.0])}
    out = rank_all_momentum(routes, ports, stocks)
    types = {r.entity_type for r in out}
    assert types == {"route", "port", "stock"}
    for r in out:
        assert math.isfinite(r.momentum_composite)
        assert r.regime in {"ACCELERATING", "DECELERATING", "SUSTAINED", "REVERSING"}
        assert r.signal in {"STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL"}


# ─── get_top_momentum ───────────────────────────────────────────────────────

def test_get_top_momentum_respects_n_argument() -> None:
    """The arg is `n` (not `limit`) — defaults to 5."""
    routes = [
        _FakeRoute(route_id=f"r{i}", rate_pct_change_30d=0.01 * i)
        for i in range(10)
    ]
    out = rank_all_momentum(routes, [], {})
    top3 = get_top_momentum(out, n=3)
    assert len(top3) == 3
    # Default of 5
    top_default = get_top_momentum(out)
    assert len(top_default) == 5


def test_get_top_momentum_filters_by_entity_type() -> None:
    routes = [_FakeRoute(route_id="r", rate_pct_change_30d=0.10)]
    ports = [_FakePort(locode="P", demand_score=0.7)]
    stocks = {"ZIM": _stock_df([15.0] * 30 + [16.0])}
    out = rank_all_momentum(routes, ports, stocks)
    only_routes = get_top_momentum(out, entity_type="route")
    assert all(r.entity_type == "route" for r in only_routes)
    only_stocks = get_top_momentum(out, entity_type="stock")
    assert all(r.entity_type == "stock" for r in only_stocks)


def test_get_top_momentum_empty_input() -> None:
    assert get_top_momentum([]) == []
