"""R125 — live-book VaR + per-scenario stress on the PERSISTED book.

Pins the persisted-book risk bridge (``processing.persisted_book_risk``):

  1. The persisted ledger loads → real marked weights + the real returns panel
     → ``portfolio_var`` runs on the REAL panel (not rng.normal), and
     ``stress_test_all_scenarios`` runs every BUILTIN scenario on the live book.
  2. A KNOWN scenario shock flows to the expected book P&L (suez_closure shocks
     ZIM +18% → a ZIM-heavy book takes a positive hit on that scenario).
  3. Empty book / dark prices → honest empty (no crash, no rng, var=None).
  4. Deterministic — same seeded book + same panel → identical numbers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from types import SimpleNamespace

from processing.persisted_book_risk import (
    PersistedBookRisk,
    load_persisted_positions,
    persisted_book_risk,
    persisted_book_stress_var,
)
from processing.stress_var import StressVaR
from state import positions as pos
from state.db import get_connection


def _idea(ticker, direction, conviction=0.8):
    """A duck-typed cascade equity idea (matches what score_equity_ideas emits)."""
    return SimpleNamespace(
        ticker=ticker, direction=direction, conviction_score=conviction)


# ── DB isolation: each test starts from an empty persisted ledger ───────────

@pytest.fixture(autouse=True)
def _clean_positions():
    conn = get_connection()
    conn.execute("DELETE FROM positions")
    conn.commit()
    yield
    conn.execute("DELETE FROM positions")
    conn.commit()


# ── Fixtures ────────────────────────────────────────────────────────────────

# A ZIM-heavy book so the suez_closure ZIM:+18% shock has a clear, sized impact.
_BOOK = [
    {"ticker": "ZIM", "sector": "Container", "shares": 1000, "avg_cost": 18.4, "beta": 1.85},
    {"ticker": "MATX", "sector": "Container", "shares": 100, "avg_cost": 100.0, "beta": 0.92},
    {"ticker": "SBLK", "sector": "Dry Bulk", "shares": 200, "avg_cost": 15.0, "beta": 1.65},
]


def _stock_data(seed: int = 7, n: int = 260) -> dict:
    """Deterministic real-shape stock_data: per-ticker close frame, DatetimeIndex.

    Mirrors the normalized cache shape ``_close_series`` ingests (a ``close``
    column on a DatetimeIndex), so ``returns_panel`` builds a genuine panel.
    Latest close is pinned per ticker so the marked weights are reproducible.
    """
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    out: dict[str, pd.DataFrame] = {}
    for t, last in (("ZIM", 22.0), ("MATX", 121.0), ("SBLK", 16.0)):
        # A gentle drift + noise path, then rescaled so the final close == last.
        path = 100.0 + rng.normal(0, 1.0, n).cumsum()
        path = path - path[-1] + last  # pin the latest close deterministically
        path = np.clip(path, 1.0, None)
        out[t] = pd.DataFrame({"close": path}, index=idx)
    return out


# ── 1. Persisted ledger → real weights + real panel → VaR + stress ──────────

def test_persisted_book_drives_real_var_and_scenarios() -> None:
    pos.replace_positions("alice", _BOOK)
    positions = load_persisted_positions("alice")
    assert {p["ticker"] for p in positions} == {"ZIM", "MATX", "SBLK"}

    risk = persisted_book_risk(positions, _stock_data())
    assert isinstance(risk, PersistedBookRisk)

    # Weights are REAL marked market values (not the equal-weight fallback).
    assert risk.weights_are_real is True
    assert risk.market_value > 0
    assert risk.n_priced == 3
    assert pytest.approx(sum(risk.weights.values()), abs=1e-6) == 1.0

    # VaR came from the REAL returns panel — a populated VaRResult, sized in $.
    assert risk.panel_is_real is True
    assert risk.panel_obs > 100
    assert risk.var is not None
    assert risk.var.n_observations > 100
    assert risk.var.var_pct <= 0.0          # a loss percentile
    assert risk.var.var_dollar > 0.0        # sized by priced market value

    # Every BUILTIN scenario ran against the live book, worst-loss-first.
    from state.scenarios import SCENARIO_CATALOG
    assert len(risk.scenarios) == len(SCENARIO_CATALOG)
    pnls = [s.pnl_pct for s in risk.scenarios]
    assert pnls == sorted(pnls), "scenarios must be sorted worst-loss-first"


# ── 2. A KNOWN scenario shock flows to the expected book P&L ────────────────

def test_known_scenario_shock_flows_to_book_pnl() -> None:
    """suez_closure shocks ZIM:+18% — a ZIM-heavy book gains on that scenario,
    and ZIM is the top dollar contributor."""
    pos.replace_positions("bob", _BOOK)
    risk = persisted_book_risk(load_persisted_positions("bob"), _stock_data())

    suez = next(s for s in risk.scenarios if s.scenario_id == "suez_closure")
    # ZIM is the only book name suez touches (+18%), so the book P&L is positive
    # and ZIM owns the contribution.
    assert suez.pnl_pct > 0.0
    assert "ZIM" in suez.per_ticker_pnl
    assert suez.per_ticker_pnl["ZIM"] > 0.0
    # Sanity on the magnitude: contribution ≈ w_ZIM * 0.18 * book_value.
    w_zim = risk.weights["ZIM"]
    expected_zim_pnl = w_zim * 0.18 * risk.market_value
    assert suez.per_ticker_pnl["ZIM"] == pytest.approx(expected_zim_pnl, rel=1e-3)
    # ZIM is the largest absolute contributor for this book.
    top_ticker = max(suez.per_ticker_pnl.items(), key=lambda kv: abs(kv[1]))[0]
    assert top_ticker == "ZIM"

    # demand_recession applies ticker:*.return ×0.82 (a -18% hit to every name)
    # → a clear book loss across the board.
    rec = next(s for s in risk.scenarios if s.scenario_id == "demand_recession")
    assert rec.pnl_pct < 0.0


# ── 3. Empty book / dark prices → honest empty (no crash, no rng) ───────────

def test_empty_book_is_honest_empty() -> None:
    risk = persisted_book_risk([], _stock_data())
    assert risk.n_positions == 0
    assert risk.weights == {}
    assert risk.weights_are_real is False
    assert risk.var is None
    assert risk.scenarios == []
    assert risk.panel_is_real is False


def test_dark_prices_no_var_but_real_scenario_shocks() -> None:
    """No price data → equal-weight fallback, var=None (no rng panel), yet the
    REAL scenario engine still stresses the (equal-weight) book."""
    risk = persisted_book_risk(_BOOK, stock_data={})  # no closes at all
    assert risk.weights_are_real is False             # equal-weight fallback
    assert risk.var is None                            # no real panel → no fabricated VaR
    assert risk.panel_is_real is False
    # Scenario shocks are still real (catalog multipliers), just on equal weights.
    assert len(risk.scenarios) > 0
    suez = next(s for s in risk.scenarios if s.scenario_id == "suez_closure")
    assert suez.per_ticker_pnl["ZIM"] > 0.0


def test_no_user_loads_empty():
    assert load_persisted_positions(None) == []
    assert load_persisted_positions("") == []


# ── 4. Determinism ──────────────────────────────────────────────────────────

def test_deterministic_across_runs() -> None:
    pos.replace_positions("carol", _BOOK)
    positions = load_persisted_positions("carol")
    a = persisted_book_risk(positions, _stock_data(seed=11))
    b = persisted_book_risk(positions, _stock_data(seed=11))
    assert a.var is not None and b.var is not None
    assert a.var.var_dollar == b.var.var_dollar
    assert a.var.var_pct == b.var.var_pct
    assert [s.pnl_pct for s in a.scenarios] == [s.pnl_pct for s in b.scenarios]
    assert a.weights == b.weights


# ── 5. The bridge does NOT fabricate (no rng.normal in the source) ──────────

def test_source_has_no_rng_fabrication() -> None:
    import inspect

    import processing.persisted_book_risk as mod
    src = inspect.getsource(mod)
    # The module docstring MENTIONS rng.normal precisely to explain that the
    # module never uses it — strip it so the prohibition bites on real CODE,
    # not on the prose that documents the prohibition.
    code = src.replace(mod.__doc__ or "", "")
    assert "np.random" not in code
    assert "rng.normal" not in code
    assert "default_rng" not in code
    # It must route through the real functions, not invent numbers.
    assert "portfolio_var" in src
    assert "stress_test_all_scenarios" in src
    assert "returns_panel" in src


# ── 6. Cascade Stress-VaR/ES on the REAL persisted book (extends R125) ───────

def test_stress_var_on_real_book_euler_and_signed() -> None:
    """The cascade Stress-VaR runs on the REAL marked book over the REAL
    covariance (basis='real-cov'), the per-name component-ES sums to es_pct
    EXACTLY (Euler), the tail is signed (es_pct <= var_pct), and the dollar
    tail is sized by the priced market value."""
    pos.replace_positions("alice", _BOOK)
    positions = load_persisted_positions("alice")
    sd = _stock_data()
    ideas = [_idea("ZIM", "Bearish", 0.9)]

    sv = persisted_book_stress_var(positions, ideas, sd, seed=7)
    assert isinstance(sv, StressVaR)
    assert sv.basis == "real-cov"           # real 260d covariance, not vol fallback
    assert sv.n_names == 3
    # Euler identity: components sum to the whole-book ES exactly.
    assert sum(sv.component_es_pct.values()) == pytest.approx(sv.es_pct, abs=1e-6)
    # Signed tail — ES is never "better" than VaR.
    assert sv.es_pct <= sv.var_pct
    # Dollar tail sized by the book's priced market value.
    risk = persisted_book_risk(positions, sd)
    assert sv.portfolio_value == pytest.approx(risk.market_value)
    assert sv.var_dollar == pytest.approx(sv.var_pct * sv.portfolio_value)


def test_stress_var_empty_book_is_none() -> None:
    assert persisted_book_stress_var([], [_idea("ZIM", "Bearish")], _stock_data()) is None


def test_stress_var_bearish_idea_flows_into_the_tail() -> None:
    """A bearish idea on a HELD name pushes the book's expected P&L negative
    vs the no-idea (pure-market) book — the live shock actually reaches the
    real book's risk, the whole point of the bridge."""
    pos.replace_positions("bob", _BOOK)
    positions = load_persisted_positions("bob")
    sd = _stock_data()

    none = persisted_book_stress_var(positions, [], sd, seed=3)
    bear = persisted_book_stress_var(
        positions, [_idea("ZIM", "Bearish", 0.9)], sd, seed=3)
    # No ideas → ~zero drift; a bearish ZIM shock drags the mean P&L down.
    assert bear.mean_pnl_pct < none.mean_pnl_pct
    # ZIM owns a real share of the (more negative) tail.
    assert bear.component_es_pct.get("ZIM", 0.0) < 0.0


def test_stress_var_deterministic_across_runs() -> None:
    pos.replace_positions("carol", _BOOK)
    positions = load_persisted_positions("carol")
    sd = _stock_data(seed=11)
    ideas = [_idea("ZIM", "Bearish", 0.7), _idea("SBLK", "Bullish", 0.5)]
    a = persisted_book_stress_var(positions, ideas, sd, seed=5)
    b = persisted_book_stress_var(positions, ideas, sd, seed=5)
    assert a.var_dollar == b.var_dollar
    assert a.es_pct == b.es_pct
    assert a.component_es_pct == b.component_es_pct
