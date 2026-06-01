"""Tests for processing.options_screener — synthetic options data + helpers."""
from __future__ import annotations

import pytest

from processing.options_screener import (
    OptionsData,
    _black_scholes_greeks,
    _generate_expiry_dates,
    calculate_max_pain,
    get_iv_surface,
    get_unusual_activity,
    screen_options,
)


# ─── OptionsData dataclass ─────────────────────────────────────────────────

def test_options_data_shape() -> None:
    o = OptionsData(
        ticker="ZIM", expiry="2026-06-15", strike=15.0, call_put="C",
        bid=1.20, ask=1.30, iv=0.85, delta=0.5, gamma=0.05,
        theta=-0.02, vega=0.10, oi=500, volume=200,
        underlying_price=14.50, moneyness=1.034,
    )
    assert o.ticker == "ZIM"
    assert o.call_put == "C"


# ─── _generate_expiry_dates ─────────────────────────────────────────────────

def test_generate_expiry_dates_returns_six_dates() -> None:
    dates = _generate_expiry_dates()
    assert len(dates) == 6
    # All ISO format
    for d in dates:
        assert len(d) == 10
        assert d[4] == "-" and d[7] == "-"


# ─── _black_scholes_greeks ──────────────────────────────────────────────────

def test_bs_greeks_call_atm() -> None:
    """At-the-money call → delta ≈ 0.5, gamma > 0, vega > 0."""
    g = _black_scholes_greeks(S=100.0, K=100.0, T=0.25, r=0.05, sigma=0.30, call_put="C")
    assert 0.4 < g["delta"] < 0.7
    assert g["gamma"] > 0
    assert g["vega"] > 0
    assert g["price"] > 0


def test_bs_greeks_put_atm() -> None:
    """At-the-money put → delta in (-0.7, -0.4)."""
    g = _black_scholes_greeks(S=100.0, K=100.0, T=0.25, r=0.05, sigma=0.30, call_put="P")
    assert -0.7 < g["delta"] < -0.3
    assert g["gamma"] > 0
    assert g["price"] > 0


def test_bs_greeks_otm_call_smaller_delta_than_itm() -> None:
    """ITM call delta > OTM call delta."""
    itm = _black_scholes_greeks(S=110.0, K=100.0, T=0.25, r=0.05, sigma=0.30, call_put="C")
    otm = _black_scholes_greeks(S=90.0, K=100.0, T=0.25, r=0.05, sigma=0.30, call_put="C")
    assert itm["delta"] > otm["delta"]


def test_bs_greeks_returns_required_keys() -> None:
    g = _black_scholes_greeks(S=100.0, K=100.0, T=0.25, r=0.05, sigma=0.30, call_put="C")
    for key in ("delta", "gamma", "theta", "vega", "price"):
        assert key in g


# ─── screen_options ────────────────────────────────────────────────────────

def test_screen_options_returns_list_of_options_data() -> None:
    out = screen_options(["ZIM"])
    assert isinstance(out, list)
    if out:
        assert all(isinstance(o, OptionsData) for o in out)


def test_screen_options_unknown_tickers_falls_back_to_full_universe() -> None:
    """Per docstring: if no valid tickers, falls back to all known tickers."""
    out = screen_options(["NEVERHEARDOFTHIS"])
    # Returns options across the full _TICKER_PRICES universe.
    tickers = {o.ticker for o in out}
    # Should include at least one of the known tickers (ZIM, MATX, etc.)
    assert tickers & {"ZIM", "MATX", "SBLK", "DAC", "STNG", "GSL"}


def test_screen_options_respects_min_oi() -> None:
    """All returned options should have oi >= min_oi."""
    out = screen_options(["ZIM"], min_oi=1000)
    for o in out:
        assert o.oi >= 1000


def test_screen_options_iv_within_bounds() -> None:
    """IV is clipped to [0.15, max_iv]."""
    out = screen_options(["ZIM"], max_iv=1.5)
    for o in out:
        assert 0.15 <= o.iv <= 1.5


def test_screen_options_bid_le_ask() -> None:
    """Bid always ≤ ask."""
    out = screen_options(["ZIM"])
    for o in out:
        assert o.bid <= o.ask


def test_screen_options_only_calls_or_puts() -> None:
    out = screen_options(["ZIM"])
    for o in out:
        assert o.call_put in ("C", "P")


# ─── get_iv_surface ────────────────────────────────────────────────────────

def test_get_iv_surface_returns_dict_with_required_keys() -> None:
    surf = get_iv_surface("ZIM")
    for key in ("ticker", "spot", "strikes", "expiries", "iv_grid"):
        assert key in surf


def test_get_iv_surface_grid_shape() -> None:
    """iv_grid is shape [n_expiries][n_strikes]."""
    surf = get_iv_surface("ZIM")
    n_expiries = len(surf["expiries"])
    n_strikes = len(surf["strikes"])
    assert len(surf["iv_grid"]) == n_expiries
    for row in surf["iv_grid"]:
        assert len(row) == n_strikes


def test_get_iv_surface_unknown_ticker_uses_defaults() -> None:
    """Unknown ticker → uses {price: 20.0, vol_base: 0.60} fallback."""
    surf = get_iv_surface("UNKNOWN_TICKER")
    assert surf["spot"] == 20.0


def test_get_iv_surface_deterministic() -> None:
    """Same ticker → same surface across calls (stable_hash seeded)."""
    a = get_iv_surface("ZIM")
    b = get_iv_surface("ZIM")
    assert a["iv_grid"] == b["iv_grid"]


# ─── get_unusual_activity ──────────────────────────────────────────────────

def _mk_opt(ticker: str, oi: int, volume: int) -> OptionsData:
    return OptionsData(
        ticker=ticker, expiry="2026-06-15", strike=15.0, call_put="C",
        bid=1.0, ask=1.1, iv=0.5, delta=0.5, gamma=0.05,
        theta=0.0, vega=0.0, oi=oi, volume=volume,
        underlying_price=15.0, moneyness=1.0,
    )


def test_unusual_activity_filters_low_ratio() -> None:
    """Only options with volume/oi ≥ 0.50 survive."""
    opts = [
        _mk_opt("ZIM", oi=100, volume=10),   # 0.10 — too low
        _mk_opt("ZIM", oi=100, volume=70),   # 0.70 — passes
        _mk_opt("ZIM", oi=100, volume=200),  # 2.00 — passes
    ]
    out = get_unusual_activity(opts)
    assert len(out) == 2
    # Sorted by ratio desc.
    assert out[0].volume == 200


def test_unusual_activity_excludes_zero_oi_or_volume() -> None:
    opts = [
        _mk_opt("ZIM", oi=0, volume=100),    # zero oi → divide guard
        _mk_opt("ZIM", oi=100, volume=0),    # zero volume → ratio 0, filtered
    ]
    out = get_unusual_activity(opts)
    assert out == []


def test_unusual_activity_empty_input() -> None:
    assert get_unusual_activity([]) == []


# ─── calculate_max_pain ────────────────────────────────────────────────────

def test_max_pain_no_options_returns_zero() -> None:
    assert calculate_max_pain([], "ZIM") == 0.0


def test_max_pain_ignores_other_tickers() -> None:
    opts = [_mk_opt("MATX", oi=100, volume=10)]
    assert calculate_max_pain(opts, "ZIM") == 0.0


def test_max_pain_returns_strike_minimizing_payout() -> None:
    """All-call portfolio: max pain is the LOWEST strike (zero ITM at all
    strikes ≤ min)."""
    opts = [
        OptionsData(
            ticker="ZIM", expiry="2026-06-15", strike=k, call_put="C",
            bid=1.0, ask=1.1, iv=0.5, delta=0.5, gamma=0.05,
            theta=0.0, vega=0.0, oi=1000, volume=0,
            underlying_price=15.0, moneyness=1.0,
        )
        for k in (10.0, 12.0, 15.0, 18.0)
    ]
    # At test_k=10, no calls are ITM (intrinsic = max(0, 10-strike) = 0 for
    # all). So total payout is 0 — minimum → max pain = 10.0.
    assert calculate_max_pain(opts, "ZIM") == 10.0


def test_max_pain_returns_strike_for_all_puts() -> None:
    """All-put portfolio: max pain is the HIGHEST strike."""
    opts = [
        OptionsData(
            ticker="ZIM", expiry="2026-06-15", strike=k, call_put="P",
            bid=1.0, ask=1.1, iv=0.5, delta=-0.5, gamma=0.05,
            theta=0.0, vega=0.0, oi=1000, volume=0,
            underlying_price=15.0, moneyness=1.0,
        )
        for k in (10.0, 12.0, 15.0, 18.0)
    ]
    # At test_k=18, no puts are ITM (intrinsic = max(0, strike-18) = 0). So
    # max pain = 18.0.
    assert calculate_max_pain(opts, "ZIM") == 18.0
