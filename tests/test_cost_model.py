"""Tests for processing.cost_model — assumed round-trip transaction costs."""
from __future__ import annotations

import pytest

from processing.cost_model import (
    DEFAULT_COST,
    DISCLAIMER,
    CostAssumption,
    cost_assumption,
    net_of_cost_pct,
    per_side_cost_bps,
    round_trip_cost_bps,
    round_trip_cost_pct,
    short_borrow_bps_per_year,
    short_borrow_cost_pct,
    turnover_cost_frac,
)


def test_round_trip_is_twice_per_side() -> None:
    c = CostAssumption(half_spread_bps=8.0, commission_bps=2.0, impact_bps=10.0)
    assert c.per_side_bps() == 20.0
    assert c.round_trip_bps() == 40.0


def test_default_cost_round_trip_40bps() -> None:
    assert DEFAULT_COST.round_trip_bps() == pytest.approx(40.0)
    assert round_trip_cost_bps("UNKNOWN_TICKER") == pytest.approx(40.0)


def test_ticker_override_tiers_ordered() -> None:
    # Liquid names cost less than thinner names.
    assert round_trip_cost_bps("ZIM") < round_trip_cost_bps("SBLK")
    assert round_trip_cost_bps("SBLK") <= round_trip_cost_bps("CMRE")
    assert round_trip_cost_bps("CMRE") < round_trip_cost_bps("GSL")


def test_case_insensitive_ticker() -> None:
    assert round_trip_cost_bps("zim") == round_trip_cost_bps("ZIM")


def test_round_trip_pct_is_bps_over_100() -> None:
    assert round_trip_cost_pct("ZIM") == pytest.approx(round_trip_cost_bps("ZIM") / 100.0)


def test_net_of_cost_subtracts_round_trip() -> None:
    # ZIM round trip = 32 bps = 0.32%. A +5% gross signal nets +4.68%.
    net = net_of_cost_pct(5.0, "ZIM")
    assert net == pytest.approx(5.0 - 0.32)


def test_net_of_cost_applies_to_negative_signed_returns() -> None:
    # A losing signal gets MORE negative — friction is paid regardless of side.
    net = net_of_cost_pct(-2.0, "ZIM")
    assert net == pytest.approx(-2.0 - 0.32)
    assert net < -2.0


def test_cost_assumption_returns_default_for_unknown() -> None:
    assert cost_assumption("ZZZ") is DEFAULT_COST


def test_disclaimer_flags_assumed_not_measured() -> None:
    assert "ASSUMED" in DISCLAIMER
    assert "not" in DISCLAIMER.lower()
    assert "borrow" in DISCLAIMER.lower()  # short-borrow now disclosed


# ── short-borrow (shorts pay financing per day held; longs do not) ───────────

def test_short_borrow_accrues_over_days() -> None:
    # 365 days of borrow == one year's rate, expressed in percent.
    assert short_borrow_cost_pct("ZIM", 365) == pytest.approx(
        short_borrow_bps_per_year("ZIM") / 100.0)
    assert short_borrow_cost_pct("ZIM", 0) == 0.0
    assert short_borrow_cost_pct("ZIM", -5) == 0.0  # clamped, never negative


def test_short_pays_borrow_on_top_of_round_trip() -> None:
    long_net = net_of_cost_pct(5.0, "SBLK")  # long: round trip only
    short_net = net_of_cost_pct(5.0, "SBLK", is_short=True, days_held=60)
    assert long_net == pytest.approx(5.0 - 0.40)  # SBLK 40 bp round trip
    assert short_net < long_net                   # borrow accrues on top


def test_thin_name_borrows_more_than_liquid() -> None:
    assert short_borrow_bps_per_year("GSL") > short_borrow_bps_per_year("ZIM")


# ── turnover cost (backtest-facing, return-fraction space) ───────────────────

def test_turnover_cost_frac_scales_with_turnover_and_rate() -> None:
    # 2 units of one-sided turnover at 20 bps/side = 40 bps = 0.004 fraction.
    assert turnover_cost_frac(2.0, per_side_bps=20.0) == pytest.approx(0.004)
    assert turnover_cost_frac(0.0, per_side_bps=20.0) == 0.0
    assert turnover_cost_frac(-1.0, per_side_bps=20.0) == 0.0  # clamped


def test_turnover_cost_frac_defaults_to_ticker_tier() -> None:
    assert turnover_cost_frac(1.0, ticker="ZIM") == pytest.approx(
        per_side_cost_bps("ZIM") / 1e4)
    # unknown ticker falls back to the default per-side cost
    assert per_side_cost_bps("ZZZ") == per_side_cost_bps(None)


def test_long_pays_no_borrow_even_with_days_held() -> None:
    # is_short defaults False — a long is unaffected by days_held.
    assert net_of_cost_pct(5.0, "ZIM", days_held=90) == pytest.approx(
        net_of_cost_pct(5.0, "ZIM"))
