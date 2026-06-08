"""R127 split-artifact-free guarantee across return/vol/correlation consumers.

The canonical stock ``close`` is now the RAW as-observed price; total returns
ride ``close * adj_factor`` (the look-ahead-free forward adjustment). A split in
the window halves/doubles the raw close, so any consumer that computes a
RETURN / %-change / volatility / correlation off the raw close would show a
spurious ~-50%/+100% artifact. These tests prove the migrated consumers DON'T:
their adjusted-path output matches the same total-return path with NO split,
while DISPLAY levels (latest price, 52w high) still equal the RAW price. They
also pin fixture-neutrality: an adj_factor-absent frame is unchanged.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Fixtures: a 2:1 split mid-window vs the same total-return path with no split
# ---------------------------------------------------------------------------

_N = 80
_SPLIT_AT = 40  # index at/after which the 2:1 split has taken effect


def _total_return_path() -> np.ndarray:
    """A deterministic, strictly-positive price path (the 'true' total return)."""
    rng = np.random.default_rng(7)
    steps = rng.normal(0.0005, 0.01, _N)
    return 100.0 * np.exp(np.cumsum(steps))


def _split_frame(start: str = "2025-01-01") -> pd.DataFrame:
    """Stock frame with a 2:1 split at row ``_SPLIT_AT``.

    RAW close halves from the split row onward; ``adj_factor`` doubles from the
    split row onward so ``close * adj_factor`` reconstructs the un-split total
    return path exactly. date is a COLUMN (the real cached shape).
    """
    path = _total_return_path()
    raw = path.copy()
    raw[_SPLIT_AT:] = raw[_SPLIT_AT:] / 2.0          # price halves on the split
    adj = np.ones(_N)
    adj[_SPLIT_AT:] = 2.0                            # factor doubles -> raw*adj == path
    return pd.DataFrame({
        "date": pd.date_range(start, periods=_N, freq="B"),
        "symbol": "ZIM",
        "close": raw,
        "adj_factor": adj,
    })


def _nosplit_frame(start: str = "2025-01-01") -> pd.DataFrame:
    """Same total-return path, but no split: raw close == path, adj_factor 1.0."""
    path = _total_return_path()
    return pd.DataFrame({
        "date": pd.date_range(start, periods=_N, freq="B"),
        "symbol": "ZIM",
        "close": path,
        "adj_factor": np.ones(_N),
    })


def _legacy_frame(start: str = "2025-01-01") -> pd.DataFrame:
    """No adj_factor column at all (fixtures / legacy frames)."""
    path = _total_return_path()
    return pd.DataFrame({
        "date": pd.date_range(start, periods=_N, freq="B"),
        "symbol": "ZIM",
        "close": path,
    })


def _to_datetime_indexed(frame: pd.DataFrame) -> pd.DataFrame:
    """date-as-index variant (some consumers want a DatetimeIndex frame)."""
    f = frame.copy()
    return f.set_index(pd.to_datetime(f["date"])).drop(columns=["date"])


# ---------------------------------------------------------------------------
# (a) book_pnl.returns_panel + day_change_pct
# ---------------------------------------------------------------------------

def test_returns_panel_is_split_artifact_free() -> None:
    from processing.book_pnl import returns_panel

    # Need a second name for the panel (>=2 cols). Re-use the same paths.
    split = {"ZIM": _split_frame(), "SBLK": _split_frame("2025-02-01")}
    nosplit = {"ZIM": _nosplit_frame(), "SBLK": _nosplit_frame("2025-02-01")}

    p_split = returns_panel(split, ["ZIM", "SBLK"], min_obs=30)
    p_nosplit = returns_panel(nosplit, ["ZIM", "SBLK"], min_obs=30)

    assert not p_split.empty and not p_nosplit.empty
    # The split must NOT inject a ~-50% (log ~-0.69) day return anywhere.
    assert p_split["ZIM"].min() > -0.3, "split artifact leaked into returns_panel"
    # Adjusted returns equal the no-split returns within tolerance.
    np.testing.assert_allclose(
        p_split["ZIM"].to_numpy(), p_nosplit["ZIM"].to_numpy(), atol=1e-9
    )


def test_day_change_pct_ignores_a_split_on_the_last_session() -> None:
    from processing.book_pnl import day_change_pct

    # Put the split on the very last row so the naive raw 1-day change is ~-50%.
    path = _total_return_path()
    raw = path.copy()
    raw[-1] = raw[-1] / 2.0
    adj = np.ones(_N)
    adj[-1] = 2.0
    frame = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=_N, freq="B"),
        "symbol": "ZIM", "close": raw, "adj_factor": adj,
    })
    chg = day_change_pct("ZIM", {"ZIM": frame})
    assert chg is not None
    assert chg > -25.0, "split read as a fake ~-50% day move"

    # No split, no adj column -> the real 1-day change is small & finite.
    chg_legacy = day_change_pct("ZIM", {"ZIM": _legacy_frame()})
    assert chg_legacy is not None and abs(chg_legacy) < 25.0


def test_book_pnl_display_level_stays_raw() -> None:
    """mark_book marks at the RAW close (the real, transactable share price)."""
    from processing.book_pnl import _latest_close, mark_book

    split = _split_frame()
    raw_last = float(split["close"].iloc[-1])
    assert _latest_close({"ZIM": split}, "ZIM") == pytest.approx(raw_last)

    book = mark_book([{"ticker": "ZIM", "shares": 10, "avg_cost": 1.0}], {"ZIM": split})
    assert book.positions[0].last_close == pytest.approx(raw_last)


# ---------------------------------------------------------------------------
# (b) signal_validation forward returns
# ---------------------------------------------------------------------------

def test_signal_validation_forward_returns_split_safe() -> None:
    from processing.signal_validation import _forward_returns, _safe_close_series

    fwd_split = _forward_returns(_safe_close_series(_split_frame()), forward_days=5, stride=1)
    fwd_nosplit = _forward_returns(_safe_close_series(_nosplit_frame()), forward_days=5, stride=1)

    assert fwd_split and fwd_nosplit
    assert min(fwd_split) > -0.3, "split artifact leaked into forward returns"
    np.testing.assert_allclose(np.array(fwd_split), np.array(fwd_nosplit), atol=1e-9)


def test_signal_validation_legacy_frame_unchanged() -> None:
    from processing.signal_validation import _safe_close_series

    s = _safe_close_series(_legacy_frame())
    assert s is not None
    np.testing.assert_allclose(s.to_numpy(), _total_return_path(), atol=1e-9)


# ---------------------------------------------------------------------------
# (c) alpha_engine._pct_change_30d
# ---------------------------------------------------------------------------

def test_alpha_engine_pct_change_30d_split_safe() -> None:
    from engine.alpha_engine import _latest_close, _pct_change_30d

    split = _split_frame()
    chg_split = _pct_change_30d({"ZIM": split}, "ZIM")
    chg_nosplit = _pct_change_30d({"ZIM": _nosplit_frame()}, "ZIM")
    assert chg_split is not None and chg_nosplit is not None
    assert chg_split > -40.0, "split read as a fake ~-50% 30d change"
    assert chg_split == pytest.approx(chg_nosplit, abs=1e-6)

    # _latest_close is the RAW transactable level.
    assert _latest_close({"ZIM": split}, "ZIM") == pytest.approx(float(split["close"].iloc[-1]))


# ---------------------------------------------------------------------------
# (d) momentum_ranker
# ---------------------------------------------------------------------------

def test_momentum_ranker_split_safe() -> None:
    from engine.momentum_ranker import _pct_change_from_df

    for days in (7, 30, 60):
        m_split = _pct_change_from_df(_split_frame(), days)
        m_nosplit = _pct_change_from_df(_nosplit_frame(), days)
        assert m_split > -0.4, f"split artifact in {days}d momentum"
        assert m_split == pytest.approx(m_nosplit, abs=1e-6)

    # Legacy (no adj_factor) frame unchanged.
    assert _pct_change_from_df(_legacy_frame(), 30) == pytest.approx(
        _pct_change_from_df(_nosplit_frame(), 30), abs=1e-9
    )


# ---------------------------------------------------------------------------
# (e) correlator
# ---------------------------------------------------------------------------

def test_correlator_split_safe() -> None:
    """A split mid-window must not change the stock<->signal correlation."""
    from engine.correlator import ShippingStockCorrelator

    n = _N
    # Build a macro signal correlated with the TRUE total-return path.
    path = _total_return_path()
    macro = {
        "BSXRLM": pd.DataFrame({
            "date": pd.date_range("2025-01-01", periods=n, freq="B"),
            "value": path * 1.5 + 3.0,   # perfectly linear in the path
        })
    }
    c = ShippingStockCorrelator(min_window=30, min_abs_r=0.1, lags_to_test=[0])
    r_split = c.analyze({"ZIM": _split_frame()}, macro)
    r_nosplit = c.analyze({"ZIM": _nosplit_frame()}, macro)

    assert r_split and r_nosplit, "expected a significant correlation both ways"
    # Same r to high precision -> the split discontinuity was removed.
    assert r_split[0].pearson_r == pytest.approx(r_nosplit[0].pearson_r, abs=1e-6)
    # And it stays a strong (near-perfect) correlation, not corrupted toward 0.
    assert abs(r_split[0].pearson_r) > 0.9


# ---------------------------------------------------------------------------
# company_profiler — %-change adjusted, display level raw
# ---------------------------------------------------------------------------

def test_company_profiler_change_adjusted_level_raw() -> None:
    from processing import company_profiler

    ticker = next(iter(company_profiler.COMPANY_PROFILES))
    split = _split_frame().assign(symbol=ticker)
    nosplit = _nosplit_frame().assign(symbol=ticker)

    prof_split = {p["ticker"]: p for p in company_profiler.compute_company_profiles({ticker: split})}[ticker]
    prof_nosplit = {p["ticker"]: p for p in company_profiler.compute_company_profiles({ticker: nosplit})}[ticker]

    # %-changes match the no-split path (split-safe) and avoid the ~-50% artifact.
    assert prof_split["change_30d"] == pytest.approx(prof_nosplit["change_30d"], abs=1e-6)
    assert prof_split["change_30d"] > -40.0
    # Display level + 52w high stay RAW (the halved real price).
    assert prof_split["price"] == pytest.approx(float(split["close"].iloc[-1]))
    assert prof_split["high_52w"] == pytest.approx(float(split["close"].max()))


# ---------------------------------------------------------------------------
# disruption_cascade._price_and_change — change adjusted, price raw
# ---------------------------------------------------------------------------

def test_disruption_cascade_price_raw_change_adjusted() -> None:
    from processing.disruption_cascade import _price_and_change

    split = _split_frame()
    price_s, chg_s = _price_and_change("ZIM", {"ZIM": split})
    price_n, chg_n = _price_and_change("ZIM", {"ZIM": _nosplit_frame()})

    assert price_s == pytest.approx(float(split["close"].iloc[-1]))   # RAW level
    assert chg_s > -0.4, "split artifact in cascade 30d change"
    assert chg_s == pytest.approx(chg_n, abs=1e-6)


# ---------------------------------------------------------------------------
# signal_ledger forward mark — split-safe signed return (dual-use site)
# ---------------------------------------------------------------------------

def test_signal_ledger_forward_return_split_safe() -> None:
    """The FROZEN issue_close (raw, pre-split) marked to a forward raw close
    after a split must not show the ~-50% artifact: the helper scales the exit
    back onto the entry's adjusted basis."""
    from state import signal_ledger

    split = _split_frame()
    nosplit = _nosplit_frame()
    issue_date = split["date"].iloc[10]            # before the split row (_SPLIT_AT=40)
    # The idea was frozen at the issue-date RAW close (no split yet -> same in both).
    issue_close = float(split["close"].iloc[10])
    assert issue_close == pytest.approx(float(nosplit["close"].iloc[10]))

    # Forward exit = the latest RAW close (halved in the split frame).
    cur_split = float(split["close"].iloc[-1])
    cur_nosplit = float(nosplit["close"].iloc[-1])

    r_split = signal_ledger._signed_forward_return(
        {"ZIM": split}, "ZIM", issue_date, issue_close, cur_split)
    r_nosplit = signal_ledger._signed_forward_return(
        {"ZIM": nosplit}, "ZIM", issue_date, issue_close, cur_nosplit)

    assert r_split is not None and r_nosplit is not None
    assert r_split > -0.4, "split artifact in ledger forward return"
    assert r_split == pytest.approx(r_nosplit, abs=1e-9)

    # Legacy (no adj_factor) reduces to the plain raw forward return.
    r_legacy = signal_ledger._signed_forward_return(
        {"ZIM": _legacy_frame()}, "ZIM", issue_date, issue_close, cur_nosplit)
    assert r_legacy == pytest.approx((cur_nosplit - issue_close) / issue_close, abs=1e-12)


def test_investor_report_30d_change_is_split_artifact_free() -> None:
    """The R127 re-review HIGH: investor_report_engine._stock_change_30d must ride
    the adjusted basis so a split INSIDE the trailing-30d window isn't a fake
    ~-50% change. Build a frame with a 2:1 split ~15 rows from the end."""
    from processing.investor_report_engine import _stock_change_30d

    n, split_at = 45, 30
    path = 100.0 * np.exp(np.cumsum(np.full(n, 0.002)))
    raw = path.copy(); raw[split_at:] = raw[split_at:] / 2.0
    adj = np.ones(n); adj[split_at:] = 2.0
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    split = {"ZIM": pd.DataFrame({"date": dates, "symbol": "ZIM",
                                  "close": raw, "adj_factor": adj})}
    nosplit = {"ZIM": pd.DataFrame({"date": dates, "symbol": "ZIM",
                                    "close": path, "adj_factor": np.ones(n)})}
    # adjusted: split frame matches the un-split total-return path (no artifact)
    assert abs(_stock_change_30d(split, "ZIM")
               - _stock_change_30d(nosplit, "ZIM")) < 1e-6
    # legacy frame (no adj_factor) would still show the large split artifact —
    # proving the fix is load-bearing.
    legacy = {"ZIM": pd.DataFrame({"date": dates, "symbol": "ZIM", "close": raw})}
    assert abs(_stock_change_30d(legacy, "ZIM")
               - _stock_change_30d(nosplit, "ZIM")) > 10.0
