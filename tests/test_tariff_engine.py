"""Tests for processing.tariff_engine (R025).

Verify the tariff pass-through / trade-diversion arithmetic on a hand-built
table so the per-commodity value-at-risk, the diversion split, and the
per-ticker roll-up are pinned to an exact, hand-checkable computation — not a
hardcoded constant.
"""
from __future__ import annotations

import math

import pytest

from processing import tariff_engine as te
from processing.tariff_engine import (
    TariffImpact,
    US_CHINA_TARIFF_RATES_2025,
    compute_default_tariff_impact,
    compute_tariff_impact,
)


# Hand-built inputs with round numbers so the arithmetic is trivial to verify.
# Two categories carrying the whole flow; one tariff rate each.
_RATES = {"electronics": 0.50, "machinery": 0.20}
_SHARES = {"electronics": 0.60, "machinery": 0.40}
# A pure-electronics ticker and a pure-machinery ticker plus a 50/50 blend.
_EXPOSURE = {
    "ELEC": {"electronics": 1.0, "machinery": 0.0},
    "MACH": {"electronics": 0.0, "machinery": 1.0},
    "BLND": {"electronics": 0.5, "machinery": 0.5},
}
_BASE = 100.0
_ELASTICITY = 1.0


def _impact():
    return compute_tariff_impact(
        _RATES, _SHARES, _EXPOSURE,
        base_trade_value_bn=_BASE,
        diversion_elasticity=_ELASTICITY,
    )


# ── Per-commodity VaR arithmetic ──────────────────────────────────────────────

def test_per_commodity_value_at_risk_matches_formula():
    imp = _impact()
    by_cat = {c.hs_category: c for c in imp.commodities}

    # Electronics: flow = 100 * 0.60 = 60 ; VaR = 0.50 * 60 = 30.
    elec = by_cat["electronics"]
    assert math.isclose(elec.trade_value_bn, 60.0)
    assert math.isclose(elec.value_at_risk_bn, 30.0)

    # Machinery: flow = 100 * 0.40 = 40 ; VaR = 0.20 * 40 = 8.
    mach = by_cat["machinery"]
    assert math.isclose(mach.trade_value_bn, 40.0)
    assert math.isclose(mach.value_at_risk_bn, 8.0)


def test_diversion_split_matches_formula():
    imp = _impact()
    by_cat = {c.hs_category: c for c in imp.commodities}

    # Electronics: divert_frac = min(1, 0.50*1.0) = 0.50.
    #   diverted = 30 * 0.50 = 15 ; direct = 30 - 15 = 15.
    elec = by_cat["electronics"]
    assert math.isclose(elec.diverted_bn, 15.0)
    assert math.isclose(elec.direct_exposure_bn, 15.0)

    # Machinery: divert_frac = min(1, 0.20*1.0) = 0.20.
    #   diverted = 8 * 0.20 = 1.6 ; direct = 8 - 1.6 = 6.4.
    mach = by_cat["machinery"]
    assert math.isclose(mach.diverted_bn, 1.6)
    assert math.isclose(mach.direct_exposure_bn, 6.4)

    # diverted + direct == VaR for every commodity.
    for c in imp.commodities:
        assert math.isclose(c.diverted_bn + c.direct_exposure_bn, c.value_at_risk_bn)


def test_diversion_fraction_is_capped_at_one():
    # A very high rate * elasticity must not divert MORE than the VaR.
    imp = compute_tariff_impact(
        {"electronics": 0.90}, {"electronics": 1.0}, {},
        base_trade_value_bn=100.0, diversion_elasticity=2.0,  # 0.9*2 = 1.8 → cap 1.0
    )
    c = imp.commodities[0]
    assert math.isclose(c.diverted_bn, c.value_at_risk_bn)  # all of it diverts
    assert math.isclose(c.direct_exposure_bn, 0.0)
    assert c.diverted_bn <= c.value_at_risk_bn + 1e-9


# ── Totals are COMPUTED sums, not constants ───────────────────────────────────

def test_total_burden_is_computed_sum():
    imp = _impact()
    # 30 (electronics) + 8 (machinery) = 38.
    assert math.isclose(imp.total_burden_bn, 38.0)
    assert math.isclose(imp.total_burden_bn, sum(c.value_at_risk_bn for c in imp.commodities))
    # NOT the old hardcoded facade.
    assert imp.total_burden_bn != 244.0

    # total trade value rolls up to the base (shares already sum to 1.0).
    assert math.isclose(imp.total_trade_value_bn, 100.0)
    # diverted + direct == burden.
    assert math.isclose(imp.total_diverted_bn + imp.total_direct_bn, imp.total_burden_bn)
    assert math.isclose(imp.total_diverted_bn, 15.0 + 1.6)
    assert math.isclose(imp.total_direct_bn, 15.0 + 6.4)


def test_shares_are_normalised_over_categories_in_play():
    # Proportional (un-normalised) shares give the same dollar split as
    # already-normalised ones — the engine normalises internally.
    a = compute_tariff_impact(_RATES, {"electronics": 6.0, "machinery": 4.0},
                              _EXPOSURE, base_trade_value_bn=_BASE,
                              diversion_elasticity=_ELASTICITY)
    b = _impact()
    assert math.isclose(a.total_burden_bn, b.total_burden_bn)
    av = {c.hs_category: c.value_at_risk_bn for c in a.commodities}
    bv = {c.hs_category: c.value_at_risk_bn for c in b.commodities}
    assert av == pytest.approx(bv)


# ── Per-ticker roll-up via the exposure map ───────────────────────────────────

def test_ticker_rollup_weights_burden_by_exposure():
    imp = _impact()
    by_t = {t.ticker: t for t in imp.tickers}

    # Pure-electronics ticker: gets electronics direct=15, diverted=15.
    elec = by_t["ELEC"]
    assert math.isclose(elec.direct_exposure_bn, 15.0)
    assert math.isclose(elec.diversion_upside_bn, 15.0)
    assert math.isclose(elec.net_bn, 0.0)

    # Pure-machinery ticker: machinery direct=6.4, diverted=1.6 → net negative.
    mach = by_t["MACH"]
    assert math.isclose(mach.direct_exposure_bn, 6.4)
    assert math.isclose(mach.diversion_upside_bn, 1.6)
    assert math.isclose(mach.net_bn, 1.6 - 6.4)
    assert mach.direction == "Bearish"

    # 50/50 blend: averages the two categories.
    blnd = by_t["BLND"]
    assert math.isclose(blnd.direct_exposure_bn, 0.5 * 15.0 + 0.5 * 6.4)
    assert math.isclose(blnd.diversion_upside_bn, 0.5 * 15.0 + 0.5 * 1.6)


def test_diversion_shifts_exposure_to_alt_origin_lanes():
    # Raising the diversion elasticity must move dollars from direct → diverted
    # (the bullish alt-origin lane) without changing the total burden.
    low = compute_tariff_impact(_RATES, _SHARES, _EXPOSURE,
                                base_trade_value_bn=_BASE, diversion_elasticity=0.4)
    high = compute_tariff_impact(_RATES, _SHARES, _EXPOSURE,
                                 base_trade_value_bn=_BASE, diversion_elasticity=1.0)
    assert math.isclose(low.total_burden_bn, high.total_burden_bn)
    assert high.total_diverted_bn > low.total_diverted_bn
    assert high.total_direct_bn < low.total_direct_bn

    # The electronics ticker's bullish diversion upside grows with elasticity.
    low_elec = {t.ticker: t for t in low.tickers}["ELEC"]
    high_elec = {t.ticker: t for t in high.tickers}["ELEC"]
    assert high_elec.diversion_upside_bn > low_elec.diversion_upside_bn


# ── Empty / degenerate inputs → zeros (no crash) ──────────────────────────────

def test_empty_inputs_return_zero_result():
    imp = compute_tariff_impact({}, {}, {})
    assert isinstance(imp, TariffImpact)
    assert imp.is_empty
    assert imp.commodities == []
    assert imp.tickers == []
    assert imp.total_burden_bn == 0.0
    assert imp.total_trade_value_bn == 0.0
    assert imp.total_diverted_bn == 0.0


def test_none_inputs_do_not_crash():
    imp = compute_tariff_impact(None, None, None)  # type: ignore[arg-type]
    assert imp.is_empty
    assert imp.total_burden_bn == 0.0


def test_all_zero_shares_return_zero_burden():
    imp = compute_tariff_impact(_RATES, {"electronics": 0.0, "machinery": 0.0},
                                _EXPOSURE, base_trade_value_bn=_BASE)
    assert imp.is_empty
    assert imp.total_burden_bn == 0.0


def test_categories_without_a_share_are_dropped():
    # A rate with no matching share contributes nothing (no fabricated flow).
    imp = compute_tariff_impact({"electronics": 0.5, "ghost": 0.9},
                                {"electronics": 1.0},
                                _EXPOSURE, base_trade_value_bn=100.0)
    cats = {c.hs_category for c in imp.commodities}
    assert cats == {"electronics"}
    assert math.isclose(imp.total_burden_bn, 50.0)


# ── Curated default regime ────────────────────────────────────────────────────

def test_default_regime_is_computed_and_nonconstant():
    imp = compute_default_tariff_impact()
    assert not imp.is_empty
    # The computed burden must be a real sum, and crucially NOT the old facade.
    assert imp.total_burden_bn > 0.0
    assert imp.total_burden_bn != 244.0
    assert math.isclose(
        imp.total_burden_bn, sum(c.value_at_risk_bn for c in imp.commodities)
    )
    # Provenance is stamped and clearly modeled-from-curated.
    assert imp.source is not None
    assert imp.source.quality == "modeled"


def test_curated_rate_table_is_sane():
    # Every curated rate is a plausible ad-valorem fraction, not a typo.
    assert US_CHINA_TARIFF_RATES_2025  # non-empty
    for cat, rate in US_CHINA_TARIFF_RATES_2025.items():
        assert 0.0 < rate < 2.0, f"{cat} rate {rate} out of sane range"


def test_policy_source_is_dated():
    assert te.TARIFF_POLICY_AS_OF is not None
    assert te.TARIFF_POLICY_SOURCE.as_of == te.TARIFF_POLICY_AS_OF
