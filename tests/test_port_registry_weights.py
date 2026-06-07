"""tests/test_port_registry_weights.py — lock in the PORT_TRAFFIC_WEIGHTS
renormalization (R017).

Background: the raw authored weights are each port's *share of national*
container traffic. For partially-covered nations they summed to < 1.0 (USA
~0.69, CHN ~0.95), so a consumer that multiplies a *national* total by the
weight silently dropped the un-tracked remainder (~31% / ~5%) — including off
the headline TEU map.

The fix renormalizes ``PORT_TRAFFIC_WEIGHTS`` so every country's port weights
sum to exactly 1.0 (the tracked ports fully distribute the national total among
themselves), while ``TRACKED_PORT_SHARE`` preserves the raw sum so the un-tracked
fraction is *explicit*, not silent.
"""
from __future__ import annotations

import math

import pytest

from ports.port_registry import (
    PORT_TRAFFIC_WEIGHTS,
    TRACKED_PORT_SHARE,
    _RAW_PORT_TRAFFIC_WEIGHTS,
    _normalize_traffic_weights,
)

# Countries whose tracked-port roster covers only part of the nation.
PARTIAL_COVERAGE_COUNTRIES = ("USA", "CHN")


# ── every country's normalized weights sum to exactly 1.0 ──────────────────


@pytest.mark.parametrize("country", sorted(PORT_TRAFFIC_WEIGHTS))
def test_normalized_weights_sum_to_one(country: str) -> None:
    """The headline invariant: no nation's traffic silently vanishes."""
    total = sum(PORT_TRAFFIC_WEIGHTS[country].values())
    assert total == pytest.approx(1.0, abs=1e-9), (
        f"{country} weights sum to {total!r}, not 1.0 — "
        f"{1.0 - total:.1%} of national traffic would vanish"
    )


# ── TRACKED_PORT_SHARE records partial coverage, < 1.0 for USA/CHN ─────────


@pytest.mark.parametrize("country", PARTIAL_COVERAGE_COUNTRIES)
def test_tracked_port_share_recorded_and_partial(country: str) -> None:
    """USA/CHN are partial-coverage — TRACKED_PORT_SHARE must be present and
    strictly < 1.0 so the un-tracked fraction is explicit, not silent."""
    assert country in TRACKED_PORT_SHARE
    share = TRACKED_PORT_SHARE[country]
    assert 0.0 < share < 1.0, f"{country} tracked share {share!r} not in (0, 1)"
    # And it equals the RAW authored sum (the honest coverage fraction).
    assert share == pytest.approx(
        sum(_RAW_PORT_TRAFFIC_WEIGHTS[country].values()), abs=1e-12
    )


def test_tracked_port_share_for_usa_chn_matches_known_coverage() -> None:
    """Pin the documented coverage so the comment can't drift again."""
    assert TRACKED_PORT_SHARE["USA"] == pytest.approx(0.69, abs=1e-9)
    assert TRACKED_PORT_SHARE["CHN"] == pytest.approx(0.95, abs=1e-9)


def test_tracked_port_share_one_for_full_coverage_countries() -> None:
    """Single-port / fully-covered countries record a tracked share of 1.0."""
    for country, share in TRACKED_PORT_SHARE.items():
        if country in PARTIAL_COVERAGE_COUNTRIES:
            continue
        assert share == pytest.approx(1.0, abs=1e-9), (
            f"{country} tracked share {share!r} != 1.0"
        )


def test_every_country_has_a_tracked_share() -> None:
    assert set(TRACKED_PORT_SHARE) == set(PORT_TRAFFIC_WEIGHTS)
    assert set(TRACKED_PORT_SHARE) == set(_RAW_PORT_TRAFFIC_WEIGHTS)


# ── no weight is negative / NaN / infinite ─────────────────────────────────


def test_no_weight_is_negative_nan_or_inf() -> None:
    for country, ports in PORT_TRAFFIC_WEIGHTS.items():
        for locode, w in ports.items():
            assert isinstance(w, float)
            assert math.isfinite(w), f"{country}/{locode} weight {w!r} not finite"
            assert w >= 0.0, f"{country}/{locode} weight {w!r} negative"
            assert 0.0 < w <= 1.0, f"{country}/{locode} weight {w!r} not in (0, 1]"


# ── renormalization is idempotent ──────────────────────────────────────────


def test_renormalization_is_idempotent() -> None:
    """Normalizing an already-normalized table is a no-op (within fp eps)."""
    once = _normalize_traffic_weights(_RAW_PORT_TRAFFIC_WEIGHTS)
    twice = _normalize_traffic_weights(once)
    assert once.keys() == twice.keys()
    for country in once:
        assert once[country].keys() == twice[country].keys()
        for locode in once[country]:
            assert once[country][locode] == pytest.approx(
                twice[country][locode], abs=1e-12
            )
    # And `PORT_TRAFFIC_WEIGHTS` itself is the fixed point.
    fixed = _normalize_traffic_weights(PORT_TRAFFIC_WEIGHTS)
    for country in PORT_TRAFFIC_WEIGHTS:
        for locode, w in PORT_TRAFFIC_WEIGHTS[country].items():
            assert fixed[country][locode] == pytest.approx(w, abs=1e-12)


def test_renormalization_preserves_relative_ratios() -> None:
    """Renormalization only rescales — the *relative* split between two tracked
    ports (what the comtrade ratio tests rely on) is unchanged."""
    raw = _RAW_PORT_TRAFFIC_WEIGHTS["CHN"]
    norm = PORT_TRAFFIC_WEIGHTS["CHN"]
    raw_ratio = raw["CNSHA"] / raw["CNNBO"]
    norm_ratio = norm["CNSHA"] / norm["CNNBO"]
    assert norm_ratio == pytest.approx(raw_ratio, abs=1e-12)


def test_normalize_handles_zero_sum_country_without_dividing_by_zero() -> None:
    """A degenerate empty/zero split is passed through, not NaN'd."""
    out = _normalize_traffic_weights({"ZZZ": {"ZZPRT": 0.0}})
    assert out["ZZZ"]["ZZPRT"] == 0.0
    assert _normalize_traffic_weights({"ZZZ": {}})["ZZZ"] == {}


# ── every weighted locode is a real tracked port ───────────────────────────


def test_weight_locodes_are_real_ports() -> None:
    from ports.port_registry import PORTS_BY_LOCODE

    for country, ports in PORT_TRAFFIC_WEIGHTS.items():
        for locode in ports:
            assert locode in PORTS_BY_LOCODE, (
                f"{country} weights reference unknown locode {locode!r}"
            )
