"""R127 — point-in-time price integrity.

``close`` is the RAW as-observed price (display = real share price); ``adj_factor``
is a FORWARD, look-ahead-free split/dividend adjustment so total returns are
``close * adj_factor`` without ever retroactively restating history (unlike
yfinance ``auto_adjust=True``, which back-restates the whole series on every
corporate action — a textbook look-ahead that inflates momentum/reversion
backtests).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from data.normalizer import STOCK_COLS, _forward_adj_factor, normalize_stock_df


def _yf_frame(closes, *, splits=None, divs=None, start="2024-01-01"):
    """A yfinance-shaped (auto_adjust=False) OHLCV frame with action columns."""
    n = len(closes)
    idx = pd.date_range(start, periods=n, freq="D")
    return pd.DataFrame(
        {
            "Open": closes, "High": closes, "Low": closes, "Close": closes,
            "Volume": [1000] * n,
            "Dividends": divs if divs is not None else [0.0] * n,
            "Stock Splits": splits if splits is not None else [0.0] * n,
        },
        index=idx,
    )


# ── adj_factor present + neutral by default ──────────────────────────────────

def test_schema_carries_adj_factor() -> None:
    assert "adj_factor" in STOCK_COLS
    out = normalize_stock_df(_yf_frame([100, 101, 102, 103]), symbol="X")
    assert list(out.columns) == STOCK_COLS


def test_no_actions_adj_factor_is_one_and_close_is_raw() -> None:
    out = normalize_stock_df(_yf_frame([100, 101, 102, 103]), symbol="X")
    assert (out["adj_factor"] == 1.0).all()
    assert list(out["close"]) == [100, 101, 102, 103]   # raw, never restated


# ── splits ───────────────────────────────────────────────────────────────────

def test_split_keeps_close_raw_and_nets_to_zero_return() -> None:
    # 2:1 split on day 3 → raw close 100→50, Stock Splits=2.0 that day.
    closes = [100, 100, 100, 50, 51]
    splits = [0, 0, 0, 2.0, 0]
    out = normalize_stock_df(_yf_frame(closes, splits=splits), symbol="X")
    assert list(out["close"]) == [100, 100, 100, 50, 51]        # RAW (display)
    assert list(out["adj_factor"]) == [1.0, 1.0, 1.0, 2.0, 2.0]
    adj = (out["close"] * out["adj_factor"]).tolist()
    assert adj == [100, 100, 100, 100, 102]                     # continuous
    rets = pd.Series(adj).pct_change().dropna().tolist()
    assert abs(rets[2]) < 1e-9                                  # split day ≈ 0%, not −50%


# ── dividends ──────────────────────────────────────────────────────────────────

def test_dividend_total_return_adds_the_cash_back() -> None:
    closes = [100, 100, 100, 99, 99]   # $1 ex-div drop on day 3
    divs = [0, 0, 0, 1.0, 0]
    out = normalize_stock_df(_yf_frame(closes, divs=divs), symbol="X")
    assert list(out["close"]) == [100, 100, 100, 99, 99]       # RAW
    adj = (out["close"] * out["adj_factor"]).tolist()
    r = adj[3] / adj[2] - 1                                     # total return on ex-div day
    assert abs(r) < 1e-9                                        # price −1 offset by +1 cash


# ── THE defining property: look-ahead freedom ─────────────────────────────────

def test_adj_factor_is_look_ahead_free() -> None:
    """Truncating the series at any date t must leave every adj_factor (and
    close) at dates ≤ t IDENTICAL — a backtest at t never sees a post-t split or
    dividend. auto_adjust=True FAILS this (it restates history retroactively)."""
    closes = [100, 100, 100, 50, 51, 52, 52]
    splits = [0, 0, 0, 2.0, 0, 0, 0]
    divs = [0, 0, 1.0, 0, 0, 0.5, 0]
    full = normalize_stock_df(_yf_frame(closes, splits=splits, divs=divs), symbol="X")
    for t in range(2, len(closes)):
        trunc = normalize_stock_df(
            _yf_frame(closes[: t + 1], splits=splits[: t + 1], divs=divs[: t + 1]),
            symbol="X",
        )
        assert np.allclose(trunc["adj_factor"].values, full["adj_factor"].values[: t + 1])
        assert list(trunc["close"]) == closes[: t + 1]


# ── the pure factor helper + degenerate guards ────────────────────────────────

def test_forward_adj_factor_base_and_recurrence() -> None:
    f = _forward_adj_factor([100, 100, 50], [0, 0, 2.0], [0, 0, 0])
    assert f[0] == 1.0 and f[1] == 1.0 and f[2] == 2.0


def test_forward_adj_factor_guards_degenerate_inputs() -> None:
    # a zero/garbage close on a dividend day must not produce inf/0/nan
    f = _forward_adj_factor([100, 0, 100], [0, 0, 0], [0, 5.0, 0])
    assert np.all(np.isfinite(f)) and np.all(f > 0)
    f2 = _forward_adj_factor([], [], [])
    assert len(f2) == 0


# ── backtest engine uses the adjusted path for returns, raw for price ─────────

def test_backtest_adj_prices_defaults_and_applies() -> None:
    from processing.backtest_engine import _adj_prices

    d = pd.DataFrame({"close": [10, 11, 12]})            # no adj_factor → raw
    assert list(_adj_prices(d)) == [10, 11, 12]
    d2 = pd.DataFrame({"close": [100, 50], "adj_factor": [1.0, 2.0]})
    assert list(_adj_prices(d2)) == [100, 100]           # close * adj_factor
