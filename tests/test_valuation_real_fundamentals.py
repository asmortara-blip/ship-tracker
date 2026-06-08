"""Tests for R047 — REAL Alpha Vantage fundamentals → ValuationInputs.

The bridge ``processing.valuation.fundamentals_to_valuation_inputs`` is pure and
offline: it takes an already-fetched ``av_data`` dict (never the network) and
populates a :class:`ValuationInputs`, stamping each field it fills from a real
AV measurement ``"real"`` and leaving every uncovered field at its assumed
default with provenance ``"assumed"``.

Defining properties pinned here:

  * dark feed (``av_data=None`` or ``{}``) → byte-for-byte the all-assumed
    ``ValuationInputs()`` default (the pre-R047 behaviour, unchanged);
  * a full real payload → covered fields populated + flagged ``"real"``,
    uncovered fields still ``"assumed"``;
  * a partial payload → a mix of ``"real"`` (covered) and ``"assumed"``;
  * sentinel-only (all 0.0, AV's missing-value coalescion) → assumed default,
    never a fabricated ``"real"`` flag;
  * a malformed payload never raises and degrades to assumed;
  * company_profiler overlays REAL fundamentals when the AV cache is warm and
    stays hardcoded/assumed when the cache is dark — CACHE-ONLY, no live fetch.
"""
from __future__ import annotations

import dataclasses

import pytest

from processing.valuation import (
    ValuationInputs,
    fundamentals_to_valuation_inputs,
)


def _as_dict(vi: ValuationInputs) -> dict:
    return dataclasses.asdict(vi)


# ─── Dark feed → byte-for-byte the assumed default ──────────────────────────

@pytest.mark.parametrize("av", [None, {}])
def test_dark_feed_is_byte_for_byte_assumed_default(av) -> None:
    """No AV data → exactly today's all-assumed ValuationInputs()."""
    default = ValuationInputs()
    got = fundamentals_to_valuation_inputs("ZIM", av_data=av)
    assert _as_dict(got) == _as_dict(default)
    assert all(v == "assumed" for v in got.input_provenance.values())


def test_sentinel_only_payload_stays_assumed() -> None:
    """AV coalesces missing values to 0.0 — a 0.0 must NOT become a 'real' flag."""
    default = ValuationInputs()
    got = fundamentals_to_valuation_inputs(
        "ZIM", av_data={"ebitda": 0.0, "market_cap_bn": 0.0, "price": 0.0}
    )
    assert _as_dict(got) == _as_dict(default)
    assert all(v == "assumed" for v in got.input_provenance.values())


# ─── Full real payload → covered fields 'real', rest 'assumed' ──────────────

def test_full_payload_populates_real_fields() -> None:
    got = fundamentals_to_valuation_inputs(
        "ZIM",
        av_data={
            "ebitda": 1200.0,
            "revenue_growth_yoy_pct": 8.0,
            "market_cap_bn": 2.5,
            "price": 20.0,
        },
    )
    # fcf_0 is DELIBERATELY left assumed (review): AV EBITDA is a PROXY for FCF,
    # is a single-QUARTER figure, and is raw USD where fcf_0 expects millions —
    # stamping it 'real' measured-annual-FCF would misrepresent + mis-scale it.
    assert got.fcf_0 == ValuationInputs().fcf_0
    # fcf_growth ← measured YoY percent → fraction
    assert got.fcf_growth == pytest.approx(0.08)
    # shares ← market_cap(USD bn) / price → millions: 2.5e9 / 20 / 1e6 = 125M
    assert got.shares_outstanding == pytest.approx(125.0)
    # provenance: only the clean, unit-safe fields are 'real'; fcf_0 stays assumed
    prov = got.input_provenance
    assert prov["fcf_0"] == "assumed"
    assert prov["fcf_growth"] == "real"
    assert prov["shares_outstanding"] == "real"
    # discount_rate / terminal_growth / net_debt stay assumed (honest: AV gives
    # no honest perpetual-growth or net-debt figure; discount needs an assumed
    # rf + ERP so it isn't measured)
    assert prov["discount_rate"] == "assumed"
    assert prov["terminal_growth"] == "assumed"
    assert prov["net_debt"] == "assumed"


def test_negative_growth_is_real_and_signed() -> None:
    """A measured negative YoY growth is real (not a sentinel) and signed."""
    got = fundamentals_to_valuation_inputs(
        "X", av_data={"revenue_growth_yoy_pct": -12.5}
    )
    assert got.fcf_growth == pytest.approx(-0.125)
    assert got.input_provenance["fcf_growth"] == "real"
    # nothing else supplied → still assumed
    assert got.input_provenance["fcf_0"] == "assumed"


# ─── Partial payload → mix of real + assumed ────────────────────────────────

def test_partial_payload_mixes_real_and_assumed() -> None:
    default = ValuationInputs()
    # A real growth + share inputs but no clean FCF input → growth/shares real,
    # fcf_0 assumed (EBITDA no longer maps to fcf_0).
    got = fundamentals_to_valuation_inputs(
        "X", av_data={"revenue_growth_yoy_pct": 8.0, "ebitda": 800.0})
    assert got.fcf_0 == default.fcf_0
    assert got.input_provenance["fcf_0"] == "assumed"
    assert got.input_provenance["fcf_growth"] == "real"


def test_ebitda_only_payload_is_all_assumed() -> None:
    """EBITDA alone no longer populates fcf_0 (proxy + quarterly + raw-USD units,
    review) — an EBITDA-only payload yields the all-assumed default."""
    default = ValuationInputs()
    got = fundamentals_to_valuation_inputs("X", av_data={"ebitda": 800.0})
    assert got.fcf_0 == default.fcf_0
    assert all(v == "assumed" for v in got.input_provenance.values())
    for f in ("fcf_growth", "discount_rate", "terminal_growth",
              "shares_outstanding", "net_debt"):
        assert got.input_provenance[f] == "assumed"


def test_market_cap_without_price_leaves_shares_assumed() -> None:
    """Shares need BOTH a real market cap and a real price; one alone → assumed."""
    default = ValuationInputs()
    only_mcap = fundamentals_to_valuation_inputs("X", av_data={"market_cap_bn": 3.0})
    assert only_mcap.shares_outstanding == default.shares_outstanding
    assert only_mcap.input_provenance["shares_outstanding"] == "assumed"

    only_price = fundamentals_to_valuation_inputs("X", av_data={"price": 30.0})
    assert only_price.shares_outstanding == default.shares_outstanding
    assert only_price.input_provenance["shares_outstanding"] == "assumed"


# ─── Robustness — never raises on malformed input ───────────────────────────

@pytest.mark.parametrize("av", [
    {"ebitda": "garbage", "market_cap_bn": None, "revenue_growth_yoy_pct": "N/A"},
    {"unknown_key": 5, "another": object()},
    {"ebitda": float("nan"), "price": float("inf")},
    {"ebitda": -500.0, "market_cap_bn": -1.0, "price": -2.0},  # negatives → not usable
    "not-a-dict",
    [],
])
def test_malformed_payload_never_raises_and_degrades_to_assumed(av) -> None:
    default = ValuationInputs()
    got = fundamentals_to_valuation_inputs("X", av_data=av)
    assert isinstance(got, ValuationInputs)
    assert _as_dict(got) == _as_dict(default)
    assert all(v == "assumed" for v in got.input_provenance.values())


def test_result_is_consumable_by_dcf() -> None:
    """A real-fundamentals ValuationInputs flows through dcf_valuation cleanly
    and the result carries the 'real' provenance forward."""
    from processing.valuation import dcf_valuation

    vi = fundamentals_to_valuation_inputs(
        "ZIM", av_data={"ebitda": 1000.0, "revenue_growth_yoy_pct": 5.0,
                        "market_cap_bn": 2.0, "price": 16.0}
    )
    res = dcf_valuation(vi, horizon=5)
    assert res.per_share_value > 0
    assert res.input_provenance["fcf_growth"] == "real"
    assert res.input_provenance["shares_outstanding"] == "real"
    # fcf_0 stays assumed (EBITDA proxy not mapped); discount always assumed
    assert res.input_provenance["fcf_0"] == "assumed"
    assert res.input_provenance["discount_rate"] == "assumed"


# ─── company_profiler cache-only overlay ────────────────────────────────────

def test_profiler_dark_cache_stays_assumed(monkeypatch) -> None:
    """With nothing cached, profiles carry no real fundamentals (assumed)."""
    import processing.company_profiler as cp

    # Force the AV helper to behave as if the cache is dark.
    monkeypatch.setattr(cp, "_av_fundamentals", lambda ticker: {})

    profiles = cp.compute_company_profiles({})
    assert profiles, "expected one profile per tracked ticker"
    for p in profiles:
        assert p["has_real_fundamentals"] is False
        prov = p["fundamentals_provenance"]
        assert prov  # present
        assert all(v == "assumed" for v in prov.values())
        # no real fundamental keys leaked onto the profile
        for k in ("market_cap_bn", "pe_ratio", "ebitda"):
            assert k not in p


def test_profiler_warm_cache_flags_real(monkeypatch) -> None:
    """When AV returns cached figures, the profile is flagged 'real' for them."""
    import processing.company_profiler as cp

    def _fake_av(ticker: str) -> dict:
        # Only ZIM has a warm cache in this fixture.
        if ticker == "ZIM":
            return {
                "market_cap_bn": 2.5,
                "pe_ratio": 4.1,
                "dividend_yield_pct": 12.0,
                "ebitda": 1200.0,
            }
        return {}

    monkeypatch.setattr(cp, "_av_fundamentals", _fake_av)

    profiles = {p["ticker"]: p for p in cp.compute_company_profiles({})}

    zim = profiles["ZIM"]
    assert zim["has_real_fundamentals"] is True
    assert zim["market_cap_bn"] == 2.5
    assert zim["pe_ratio"] == 4.1
    assert zim["ebitda"] == 1200.0
    prov = zim["fundamentals_provenance"]
    assert prov["market_cap_bn"] == "real"
    assert prov["pe_ratio"] == "real"
    assert prov["ebitda"] == "real"
    # a field AV did not return stays assumed
    assert prov["beta"] == "assumed"

    # A ticker with a dark cache stays fully assumed.
    other = profiles["MATX"]
    assert other["has_real_fundamentals"] is False
    assert all(v == "assumed" for v in other["fundamentals_provenance"].values())


def test_av_fundamentals_no_key_is_empty(monkeypatch) -> None:
    """No API key → cache-only helper returns {} (never fetches)."""
    import processing.company_profiler as cp
    import data.alphavantage_feed as av

    monkeypatch.setattr(av, "_get_api_key", lambda: "")
    assert cp._av_fundamentals("ZIM") == {}


def test_av_fundamentals_never_force_fetches(monkeypatch) -> None:
    """Even with a key set, a cold cache must NOT trigger a live fetch."""
    import processing.company_profiler as cp
    import data.alphavantage_feed as av

    monkeypatch.setattr(av, "_get_api_key", lambda: "DUMMYKEY")
    # is_cached → always False (cold cache): the helper must short-circuit and
    # never call fetch_fundamentals / fetch_income.
    from data.cache_manager import CacheManager
    monkeypatch.setattr(CacheManager, "is_cached", lambda self, *a, **k: False)

    def _boom(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("live fetch attempted on a cold cache!")

    monkeypatch.setattr(av, "fetch_fundamentals", _boom)
    monkeypatch.setattr(av, "fetch_income", _boom)

    assert cp._av_fundamentals("ZIM") == {}
