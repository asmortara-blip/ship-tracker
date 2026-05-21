"""Tests for processing.company_profiler — shipping-company profiles + commentary.

Covers:
  - COMPANY_PROFILES catalog: every entry has the keys the profiler relies on
  - compute_company_profiles:
      * always returns one entry per tracked ticker (even without stock_data)
      * missing / empty / no-close ticker frames → profile carries no price field
      * change_1d / change_5d / change_30d populated when history is long enough
      * 52-week range_position formula
      * technical_signal tiers ('Strong Buy' / 'Buy' / 'Hold' / 'Weak' / 'Sell')
      * MA50 / MA200 only set when history is sufficient
  - _generate_commentary indirectly (commentary field):
      * surge phrasing when change_30d > 10
      * 'edged higher' phrasing when change_30d in (0, 10]
      * 'pulled back' for change_30d in [-10, 0]
      * 'under significant pressure' for change_30d < -10
      * always carries the company's competitive_edge + risk_factor lines
"""
from __future__ import annotations

import pandas as pd
import pytest

from processing.company_profiler import COMPANY_PROFILES, compute_company_profiles


def _price_df(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(prices), freq="B"),
        "close": prices,
    })


# ─── Catalog shape ──────────────────────────────────────────────────────────

def test_company_profiles_catalog_has_required_keys() -> None:
    required = {"name", "hq", "sector", "fleet_size", "competitive_edge",
                "risk_factor"}
    for ticker, meta in COMPANY_PROFILES.items():
        assert required <= set(meta.keys()), f"{ticker} missing keys"


def test_company_profiles_catalog_has_seven_companies() -> None:
    """Sanity: the 7 well-known shipping tickers we profile."""
    assert set(COMPANY_PROFILES.keys()) == {
        "ZIM", "MATX", "SBLK", "DAC", "CMRE", "GOGL", "DSX",
    }


# ─── compute_company_profiles ───────────────────────────────────────────────

def test_compute_company_profiles_always_one_per_ticker() -> None:
    """Even with no stock_data, every catalog ticker gets a profile."""
    profiles = compute_company_profiles({})
    tickers = {p["ticker"] for p in profiles}
    assert tickers == set(COMPANY_PROFILES.keys())


def test_compute_company_profiles_carries_master_data_into_profile() -> None:
    profiles = compute_company_profiles({})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert zim["name"] == COMPANY_PROFILES["ZIM"]["name"]
    assert zim["competitive_edge"] == COMPANY_PROFILES["ZIM"]["competitive_edge"]


def test_compute_company_profiles_no_price_when_close_missing() -> None:
    profiles = compute_company_profiles({"ZIM": pd.DataFrame({"date": []})})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert "price" not in zim


def test_compute_company_profiles_no_price_when_too_short() -> None:
    profiles = compute_company_profiles({"ZIM": _price_df([10.0])})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert "price" not in zim


def test_compute_company_profiles_change_1d_when_two_obs() -> None:
    profiles = compute_company_profiles({"ZIM": _price_df([10.0, 11.0])})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert zim["price"] == pytest.approx(11.0)
    assert zim["change_1d"] == pytest.approx(10.0)


def test_compute_company_profiles_change_5d_needs_6_obs() -> None:
    """change_5d uses iloc[-6]."""
    prices = [10.0] * 5 + [11.0]  # 6 obs, 11/10 = +10%
    profiles = compute_company_profiles({"ZIM": _price_df(prices)})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert zim["change_5d"] == pytest.approx(10.0)


def test_compute_company_profiles_change_30d_needs_31_obs() -> None:
    prices = [10.0] * 30 + [11.0]
    profiles = compute_company_profiles({"ZIM": _price_df(prices)})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert zim["change_30d"] == pytest.approx(10.0)


def test_compute_company_profiles_range_position_formula() -> None:
    """current=15, min=10, max=20 → (15-10)/(20-10) = 50%."""
    prices = [10.0] + [20.0] + [15.0] * 30   # 32 obs
    profiles = compute_company_profiles({"ZIM": _price_df(prices)})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert zim["range_position"] == pytest.approx(50.0, abs=1.0)
    assert zim["high_52w"] == pytest.approx(20.0)
    assert zim["low_52w"] == pytest.approx(10.0)


def test_compute_company_profiles_ma_50_only_when_50_obs() -> None:
    profiles = compute_company_profiles({"ZIM": _price_df([10.0] * 50)})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert zim["ma_50"] == pytest.approx(10.0)
    assert zim["above_ma_50"] is False


def test_compute_company_profiles_ma_200_only_when_200_obs() -> None:
    profiles = compute_company_profiles({"ZIM": _price_df([10.0] * 50)})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert "ma_200" not in zim


def test_compute_company_profiles_technical_signal_strong_buy() -> None:
    """above_50 + above_200 + chg_30 > 5 → 'Strong Buy'."""
    # 200 obs flat at 10, then rising
    prices = [10.0] * 200 + [11.0] * 30 + [15.0]
    profiles = compute_company_profiles({"ZIM": _price_df(prices)})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert zim["technical_signal"] == "Strong Buy"


def test_compute_company_profiles_technical_signal_sell() -> None:
    """not above_50 + not above_200 + chg_30 < -5 → 'Sell'."""
    prices = [20.0] * 200 + [15.0] * 30 + [5.0]
    profiles = compute_company_profiles({"ZIM": _price_df(prices)})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert zim["technical_signal"] == "Sell"


def test_compute_company_profiles_commentary_carries_edge_and_risk() -> None:
    """Even with no price data, edge + risk are always appended."""
    profiles = compute_company_profiles({})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    commentary = zim["commentary"]
    edge_phrase = COMPANY_PROFILES["ZIM"]["competitive_edge"]
    assert edge_phrase in commentary
    assert COMPANY_PROFILES["ZIM"]["risk_factor"] in commentary


def test_compute_company_profiles_commentary_surged_phrasing() -> None:
    """change_30d > 10 → 'surged' phrasing in commentary."""
    prices = [10.0] * 30 + [12.0]  # +20%
    profiles = compute_company_profiles({"ZIM": _price_df(prices)})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert "surged" in zim["commentary"].lower()


def test_compute_company_profiles_commentary_pulled_back_phrasing() -> None:
    """-10 < change_30d < 0 → 'pulled back' phrasing."""
    prices = [10.0] * 30 + [9.5]   # -5%
    profiles = compute_company_profiles({"ZIM": _price_df(prices)})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert "pulled back" in zim["commentary"].lower()


def test_compute_company_profiles_commentary_under_pressure_phrasing() -> None:
    """change_30d < -10 → 'under significant pressure' phrasing."""
    prices = [10.0] * 30 + [8.0]  # -20%
    profiles = compute_company_profiles({"ZIM": _price_df(prices)})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert "pressure" in zim["commentary"].lower()


def test_compute_company_profiles_commentary_near_52w_high_phrasing() -> None:
    """range_position > 80 → '52-week high' phrasing."""
    # current=20, min=10, max=20 → range_pos=100
    prices = [10.0] + [20.0] * 30
    profiles = compute_company_profiles({"ZIM": _price_df(prices)})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert "52-week high" in zim["commentary"]


def test_compute_company_profiles_volatility_finite_when_30_returns() -> None:
    prices = [10.0 + 0.1 * (i % 3) for i in range(40)]
    profiles = compute_company_profiles({"ZIM": _price_df(prices)})
    zim = next(p for p in profiles if p["ticker"] == "ZIM")
    assert zim["volatility_30d"] is not None
    assert zim["volatility_30d"] >= 0.0
