"""Coherent, cascade-grounded Stress-VaR / ES engine (rec R009)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


def _idea(ticker, direction, conviction=0.8):
    return SimpleNamespace(
        ticker=ticker, direction=direction, conviction_score=conviction)


def _real_panel(tickers, *, periods=200, seed=1):
    """stock_data with enough cached closes for the real-cov path (min_obs=60)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=periods, freq="B")
    out = {}
    for i, t in enumerate(tickers):
        steps = rng.normal(0.0005, 0.02, size=periods)
        closes = 100.0 * np.exp(np.cumsum(steps))
        out[t] = pd.DataFrame({"close": closes}, index=idx)
    return out


# ── cascade_return_shocks ───────────────────────────────────────────────────

def test_shocks_sign_and_magnitude() -> None:
    from processing.stress_var import cascade_return_shocks
    sh = cascade_return_shocks(
        [_idea("ZIM", "Bullish", 1.0), _idea("SBLK", "Bearish", 0.5),
         _idea("DAC", "Neutral", 0.9)],
        severity=1.0, max_shock=0.20)
    assert sh["ZIM"] == pytest.approx(0.20)     # +1 * 1.0 * 0.20
    assert sh["SBLK"] == pytest.approx(-0.10)   # -1 * 0.5 * 0.20
    assert "DAC" not in sh                       # Neutral dropped


def test_shocks_clamped_and_severity_scaled() -> None:
    from processing.stress_var import cascade_return_shocks
    # conviction>1 (shouldn't happen) is clamped; severity scales the move.
    sh = cascade_return_shocks([_idea("ZIM", "Bullish", 5.0)],
                               severity=2.0, max_shock=0.20)
    assert sh["ZIM"] == pytest.approx(0.20)      # clamped to +max_shock
    sh2 = cascade_return_shocks([_idea("ZIM", "Bullish", 0.5)],
                                severity=0.5, max_shock=0.20)
    assert sh2["ZIM"] == pytest.approx(0.05)     # 0.5 * 0.5 * 0.20


# ── monte_carlo_book_es ─────────────────────────────────────────────────────

def test_empty_book_is_zeroed() -> None:
    from processing.stress_var import monte_carlo_book_es
    r = monte_carlo_book_es({}, [], {})
    assert r.basis == "empty" and r.n_names == 0
    assert r.var_pct == 0.0 and r.es_pct == 0.0 and r.es_dollar == 0.0


def test_component_es_sums_to_es_exactly() -> None:
    # The Euler decomposition keystone: per-name components sum to ES.
    from processing.stress_var import monte_carlo_book_es
    weights = {"ZIM": 0.5, "SBLK": 0.3, "DAC": 0.2}
    ideas = [_idea("ZIM", "Bearish", 0.9), _idea("SBLK", "Bullish", 0.4)]
    r = monte_carlo_book_es(weights, ideas, {}, n_sims=20_000, seed=7)
    assert sum(r.component_es_pct.values()) == pytest.approx(r.es_pct, abs=1e-9)
    assert set(r.component_es_pct) == set(weights)


def test_es_is_at_least_as_severe_as_var() -> None:
    # Coherence: ES ≤ VaR ≤ 0 for a no-shock (mean-zero) book.
    from processing.stress_var import monte_carlo_book_es
    r = monte_carlo_book_es({"ZIM": 0.6, "SBLK": 0.4}, [], {},
                            n_sims=30_000, seed=3)
    assert r.es_pct <= r.var_pct <= 1e-9


def test_bearish_idea_tilts_a_long_book_down() -> None:
    from processing.stress_var import monte_carlo_book_es
    base = monte_carlo_book_es({"ZIM": 1.0}, [], {}, n_sims=40_000, seed=5)
    shocked = monte_carlo_book_es({"ZIM": 1.0}, [_idea("ZIM", "Bearish", 1.0)],
                                  {}, n_sims=40_000, seed=5, max_shock=0.20)
    assert abs(base.mean_pnl_pct) < 0.02              # no shock -> ~flat mean
    assert shocked.mean_pnl_pct < -0.15               # bearish shock -> down
    assert shocked.es_pct < base.es_pct               # deeper tail loss


def test_dollar_figures_track_pct() -> None:
    from processing.stress_var import monte_carlo_book_es
    r = monte_carlo_book_es({"ZIM": 0.5, "SBLK": 0.5}, [], {},
                            n_sims=10_000, portfolio_value=2_000_000.0, seed=1)
    assert r.var_dollar == pytest.approx(abs(r.var_pct) * 2_000_000.0)
    assert r.es_dollar == pytest.approx(abs(r.es_pct) * 2_000_000.0)


def test_deterministic_under_seed() -> None:
    from processing.stress_var import monte_carlo_book_es
    kw = dict(n_sims=5_000, seed=11)
    a = monte_carlo_book_es({"ZIM": 0.5, "SBLK": 0.5}, [], {}, **kw)
    b = monte_carlo_book_es({"ZIM": 0.5, "SBLK": 0.5}, [], {}, **kw)
    assert a.var_pct == b.var_pct and a.es_pct == b.es_pct


def test_basis_is_diagonal_without_prices_real_with() -> None:
    from processing.stress_var import monte_carlo_book_es
    dark = monte_carlo_book_es({"ZIM": 0.5, "SBLK": 0.5}, [], {}, n_sims=2_000)
    assert dark.basis == "diagonal-vol"
    real = monte_carlo_book_es({"ZIM": 0.5, "SBLK": 0.5}, [],
                               _real_panel(["ZIM", "SBLK"]), n_sims=2_000)
    assert real.basis == "real-cov"


def test_never_raises_on_bad_inputs() -> None:
    from processing.stress_var import monte_carlo_book_es
    r = monte_carlo_book_es({"ZIM": 1.0}, None, None, n_sims=500)
    assert r.n_names == 1 and r.basis == "diagonal-vol"


# ── per-driver tail attribution (R065) ──────────────────────────────────────

def _idea_d(ticker, direction, driver, conviction=0.8):
    return SimpleNamespace(ticker=ticker, direction=direction,
                           conviction_score=conviction,
                           dominant_driver_key=driver)


def test_idea_driver_map_extracts_keys() -> None:
    from processing.stress_var import idea_driver_map
    m = idea_driver_map([_idea_d("ZIM", "Bullish", "chokepoint"),
                         _idea_d("SBLK", "Bearish", "rate"),
                         SimpleNamespace(ticker="", direction="Bullish")])
    assert m == {"ZIM": "chokepoint", "SBLK": "rate"}


def test_component_es_by_driver_buckets_and_defaults() -> None:
    from processing.stress_var import component_es_by_driver
    comp = {"ZIM": -2.0, "MATX": -1.0, "SBLK": -0.5, "DAC": -0.25}
    td = {"ZIM": "chokepoint", "MATX": "chokepoint", "SBLK": "rate"}  # DAC absent
    by = component_es_by_driver(comp, td)
    assert by["chokepoint"] == pytest.approx(-3.0)   # ZIM + MATX
    assert by["rate"] == pytest.approx(-0.5)
    assert by["market"] == pytest.approx(-0.25)      # DAC -> default bucket


def test_driver_buckets_sum_to_es_exactly() -> None:
    # The R065 payoff must preserve the R009 invariant: driver buckets are a
    # partition of the per-name components, so they sum to ES exactly.
    from processing.stress_var import (
        component_es_by_driver, idea_driver_map, monte_carlo_book_es,
    )
    weights = {"ZIM": 0.4, "MATX": 0.3, "SBLK": 0.2, "DAC": 0.1}
    ideas = [_idea_d("ZIM", "Bearish", "chokepoint", 0.9),
             _idea_d("MATX", "Bearish", "chokepoint", 0.6),
             _idea_d("SBLK", "Bullish", "rate", 0.5)]   # DAC unshocked -> market
    r = monte_carlo_book_es(weights, ideas, {}, n_sims=20_000, seed=4)
    by = component_es_by_driver(r.component_es_pct, idea_driver_map(ideas))
    assert sum(by.values()) == pytest.approx(r.es_pct, abs=1e-9)
    assert "chokepoint" in by and "market" in by
