"""Risk Lab uses REAL cached returns, synthetic only as a labeled fallback (R021)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ui.tab_risk_lab import _real_returns_panel


def _frame(prices, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(prices), freq="B")
    return pd.DataFrame({"close": prices}, index=idx)


def _stock(tickers, n=200, seed=1):
    rng = np.random.default_rng(seed)
    return {t: _frame(100 + rng.normal(0, 1, n).cumsum()) for t in tickers}


def test_real_panel_built_from_cached_closes() -> None:
    panel = _real_returns_panel(_stock(["ZIM", "MATX", "SBLK"]),
                                ["ZIM", "MATX", "SBLK", "DAC"])  # DAC has no data
    assert not panel.empty
    assert set(panel.columns) == {"ZIM", "MATX", "SBLK"}  # only tickers with data
    assert len(panel) > 100
    assert isinstance(panel.index, pd.DatetimeIndex)


def test_real_panel_has_real_covariance_not_fixed_030() -> None:
    # The whole point of R021: the panel reflects ACTUAL correlations, not the
    # synthetic 0.30 uniform corr. Two correlated tickers should show it.
    rng = np.random.default_rng(7)
    base = rng.normal(0, 1, 200).cumsum()
    stock = {
        "A": _frame(100 + base),
        "B": _frame(100 + base + rng.normal(0, 0.1, 200)),  # near-duplicate of A
        "C": _frame(100 + rng.normal(0, 1, 200).cumsum()),  # independent
    }
    panel = _real_returns_panel(stock, ["A", "B", "C"])
    corr = panel.corr()
    assert corr.loc["A", "B"] > 0.8           # genuinely high
    assert abs(corr.loc["A", "C"]) < 0.5      # genuinely low — not a fixed 0.30


def test_falls_back_when_insufficient_real_data() -> None:
    assert _real_returns_panel(_stock(["ZIM"]), ["ZIM", "MATX"]).empty  # 1 ticker
    assert _real_returns_panel(None, ["ZIM"]).empty                     # no dict
    assert _real_returns_panel(                                          # too short
        {t: _frame([10, 11, 12]) for t in ("ZIM", "MATX")}, ["ZIM", "MATX"]
    ).empty
