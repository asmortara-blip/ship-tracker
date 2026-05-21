"""Tests for data.currency_feed.

The module pulls FX spot + history from yfinance, caches them through
CacheManager, and falls back to hardcoded defaults when yfinance is
unavailable or returns empty. These tests pin the **defining properties**
without ever touching the network:

  - Configuration tables
      * KEY_CURRENCIES holds the six expected USD-base pairs
      * _YF_TICKER_MAP has one ticker per key pair
      * _DEFAULTS has a positive float for every key pair
      * EUR is sourced from the inverted EURUSD=X ticker

  - _defaults_as_df
      * Returns a single-row DataFrame whose columns are exactly the key pairs
      * Values match _DEFAULTS

  - _fetch_spot_rates_df  (yf.download monkeypatched)
      * yfinance unavailable → returns the defaults frame
      * yfinance empty → returns the defaults frame
      * yfinance raises → returns the defaults frame
      * Happy path → one row, six pairs, all rounded floats
      * EUR is inverted: yfinance EURUSD≈1.08 ⇒ stored USD/EUR ≈ 1/1.08
      * EURUSD=0 path falls back to _DEFAULTS["USD/EUR"] without dividing by zero
      * Missing ticker rows are filled with defaults

  - _fetch_history_df  (yf.download monkeypatched)
      * yfinance unavailable → empty DataFrame
      * yfinance empty → empty DataFrame
      * yfinance raises → empty DataFrame
      * Happy path → tidy long format with columns [date, close, pair]
      * date column is tz-naive, normalized
      * EUR rows are inverted (USD/EUR ≈ 1/EURUSD)
      * EURUSD=0 row is replaced with the default, no ZeroDivisionError

  - fetch_fx_rates  (public API, CacheManager isolated under tmp_path)
      * Cache miss: invokes the internal fetch, returns dict keyed by pair
      * Cache hit:  second call does NOT re-invoke yfinance
      * Total fetch failure: falls back to dict(_DEFAULTS)
      * Returned dict keys are a subset of KEY_CURRENCIES (filters unknown cols)

  - fetch_fx_history  (public API, CacheManager isolated under tmp_path)
      * Cache miss: invokes yf.download once
      * Cache hit:  subsequent call does not re-invoke yf.download
      * Empty fetch → every pair maps to an empty DataFrame
      * Each non-empty per-pair DataFrame has columns [date, close, pair]
        and only contains rows for that pair
"""
from __future__ import annotations

import pandas as pd
import pytest

from data import currency_feed as cf
from data.cache_manager import CacheManager


# ─── Helpers ────────────────────────────────────────────────────────────────

# Stable, deterministic values for the yfinance mock.
# yfinance gives EURUSD (≈1.08), the module inverts it to USD/EUR (~0.926).
_MOCK_LAST_CLOSE = {
    "USDCNY=X": 7.25,
    "EURUSD=X": 1.08,       # will be inverted by the module
    "USDKRW=X": 1350.0,
    "USDJPY=X": 150.0,
    "USDBRL=X": 5.12,
    "USDSGD=X": 1.36,
}


def _make_yf_frame(last_values: dict[str, float], n_days: int = 5) -> pd.DataFrame:
    """Build a yfinance-shaped DataFrame: MultiIndex (metric, ticker) columns.

    Rows are dated; the **last row** holds the values the module pulls for
    spot rates. For history tests, every row has the same close (simpler to
    assert against).
    """
    idx = pd.date_range("2026-04-01", periods=n_days, freq="D")
    tickers = list(last_values.keys())
    cols = pd.MultiIndex.from_product([["Close"], tickers])
    data = [[last_values[t] for t in tickers] for _ in range(n_days)]
    return pd.DataFrame(data, index=idx, columns=cols)


def _patch_yf(monkeypatch, frame: pd.DataFrame) -> dict:
    """Patch yf.download to return `frame`. Returns a call-counter dict."""
    state = {"calls": 0}

    def _fake_download(*args, **kwargs):
        state["calls"] += 1
        return frame

    # Force the module to think yfinance is available, then patch the
    # download call it sees.
    monkeypatch.setattr(cf, "_YFINANCE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(cf, "yf", _FakeYF(_fake_download), raising=False)
    return state


class _FakeYF:
    """Tiny stand-in for the yfinance module exposing only `.download`."""

    def __init__(self, download_fn):
        self.download = download_fn


# ─── Configuration tables ───────────────────────────────────────────────────

def test_key_currencies_shape():
    expected = {"USD/CNY", "USD/EUR", "USD/KRW", "USD/JPY", "USD/BRL", "USD/SGD"}
    assert set(cf.KEY_CURRENCIES) == expected
    for pair, meta in cf.KEY_CURRENCIES.items():
        assert "name" in meta and "shipping_impact" in meta
        assert isinstance(meta["name"], str) and meta["name"]
        assert isinstance(meta["shipping_impact"], str) and meta["shipping_impact"]


def test_ticker_map_covers_every_pair():
    # Every key pair has exactly one ticker; EUR comes from EURUSD=X
    # (which the module inverts).
    assert set(cf._YF_TICKER_MAP.values()) == set(cf.KEY_CURRENCIES)
    assert "EURUSD=X" in cf._YF_TICKER_MAP
    assert cf._YF_TICKER_MAP["EURUSD=X"] == "USD/EUR"
    assert cf._EURUSD_TICKER == "EURUSD=X"


def test_defaults_have_positive_floats_for_every_pair():
    assert set(cf._DEFAULTS) == set(cf.KEY_CURRENCIES)
    for pair, val in cf._DEFAULTS.items():
        assert isinstance(val, float)
        assert val > 0


# ─── _defaults_as_df ────────────────────────────────────────────────────────

def test_defaults_as_df_shape_and_values():
    df = cf._defaults_as_df()
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert set(df.columns) == set(cf._DEFAULTS)
    row = df.iloc[0]
    for pair, default in cf._DEFAULTS.items():
        assert float(row[pair]) == pytest.approx(default)


# ─── _fetch_spot_rates_df: failure modes ───────────────────────────────────

def test_spot_returns_defaults_when_yfinance_unavailable(monkeypatch):
    monkeypatch.setattr(cf, "_YFINANCE_AVAILABLE", False, raising=False)
    df = cf._fetch_spot_rates_df()
    assert len(df) == 1
    # Same shape as defaults frame.
    for pair, default in cf._DEFAULTS.items():
        assert float(df.iloc[0][pair]) == pytest.approx(default)


def test_spot_returns_defaults_when_yfinance_empty(monkeypatch):
    _patch_yf(monkeypatch, pd.DataFrame())  # empty
    df = cf._fetch_spot_rates_df()
    for pair, default in cf._DEFAULTS.items():
        assert float(df.iloc[0][pair]) == pytest.approx(default)


def test_spot_returns_defaults_when_yfinance_raises(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(cf, "_YFINANCE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(cf, "yf", _FakeYF(_boom), raising=False)

    df = cf._fetch_spot_rates_df()
    for pair, default in cf._DEFAULTS.items():
        assert float(df.iloc[0][pair]) == pytest.approx(default)


# ─── _fetch_spot_rates_df: happy + inversion ───────────────────────────────

def test_spot_happy_path_all_pairs_present(monkeypatch):
    _patch_yf(monkeypatch, _make_yf_frame(_MOCK_LAST_CLOSE))
    df = cf._fetch_spot_rates_df()

    assert len(df) == 1
    row = df.iloc[0]
    for pair in cf.KEY_CURRENCIES:
        assert pair in df.columns
        assert float(row[pair]) > 0


def test_spot_inverts_eurusd_to_usd_eur(monkeypatch):
    """yfinance gives EURUSD≈1.08, module stores USD/EUR≈1/1.08."""
    _patch_yf(monkeypatch, _make_yf_frame(_MOCK_LAST_CLOSE))
    df = cf._fetch_spot_rates_df()
    eur = float(df.iloc[0]["USD/EUR"])
    assert eur == pytest.approx(round(1.0 / 1.08, 6), abs=1e-6)
    # And it's clearly *not* the raw EURUSD value:
    assert eur < 1.0


def test_spot_eurusd_zero_falls_back_to_default(monkeypatch):
    """Division-by-zero guard: EURUSD=0 must yield _DEFAULTS["USD/EUR"]."""
    bad = dict(_MOCK_LAST_CLOSE)
    bad["EURUSD=X"] = 0.0
    _patch_yf(monkeypatch, _make_yf_frame(bad))
    df = cf._fetch_spot_rates_df()
    assert float(df.iloc[0]["USD/EUR"]) == pytest.approx(cf._DEFAULTS["USD/EUR"])


def test_spot_missing_ticker_filled_with_default(monkeypatch):
    """If yfinance returns data for only some tickers, missing pairs get defaults."""
    partial = {"USDCNY=X": 7.30, "USDJPY=X": 151.5}  # 4 tickers missing
    _patch_yf(monkeypatch, _make_yf_frame(partial))
    df = cf._fetch_spot_rates_df()

    row = df.iloc[0]
    # Provided ones reflect mocked values (rounded to 6 dp).
    assert float(row["USD/CNY"]) == pytest.approx(7.30)
    assert float(row["USD/JPY"]) == pytest.approx(151.5)
    # Missing ones fall back to defaults.
    assert float(row["USD/EUR"]) == pytest.approx(cf._DEFAULTS["USD/EUR"])
    assert float(row["USD/KRW"]) == pytest.approx(cf._DEFAULTS["USD/KRW"])
    assert float(row["USD/BRL"]) == pytest.approx(cf._DEFAULTS["USD/BRL"])
    assert float(row["USD/SGD"]) == pytest.approx(cf._DEFAULTS["USD/SGD"])


# ─── _fetch_history_df: failure modes ──────────────────────────────────────

def test_history_returns_empty_when_yfinance_unavailable(monkeypatch):
    monkeypatch.setattr(cf, "_YFINANCE_AVAILABLE", False, raising=False)
    out = cf._fetch_history_df(list(cf._YF_TICKER_MAP), lookback_days=30)
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_history_returns_empty_when_yfinance_empty(monkeypatch):
    _patch_yf(monkeypatch, pd.DataFrame())
    out = cf._fetch_history_df(list(cf._YF_TICKER_MAP), lookback_days=30)
    assert out.empty


def test_history_returns_empty_when_yfinance_raises(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("api gone")

    monkeypatch.setattr(cf, "_YFINANCE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(cf, "yf", _FakeYF(_boom), raising=False)
    out = cf._fetch_history_df(list(cf._YF_TICKER_MAP), lookback_days=30)
    assert out.empty


# ─── _fetch_history_df: happy + inversion ──────────────────────────────────

def test_history_happy_path_tidy_long_format(monkeypatch):
    frame = _make_yf_frame(_MOCK_LAST_CLOSE, n_days=7)
    _patch_yf(monkeypatch, frame)
    out = cf._fetch_history_df(list(cf._YF_TICKER_MAP), lookback_days=10)

    assert not out.empty
    assert set(out.columns) == {"date", "close", "pair"}
    # 7 days × 6 pairs.
    assert len(out) == 7 * len(cf.KEY_CURRENCIES)
    assert set(out["pair"]) == set(cf.KEY_CURRENCIES)
    # Dates are tz-naive and normalized.
    assert out["date"].dt.tz is None
    assert (out["date"] == out["date"].dt.normalize()).all()


def test_history_inverts_eur_rows(monkeypatch):
    """USD/EUR rows in the long frame must equal 1/EURUSD (rounded)."""
    frame = _make_yf_frame(_MOCK_LAST_CLOSE, n_days=4)
    _patch_yf(monkeypatch, frame)
    out = cf._fetch_history_df(list(cf._YF_TICKER_MAP), lookback_days=10)

    eur_rows = out[out["pair"] == "USD/EUR"]
    assert len(eur_rows) == 4
    expected = round(1.0 / 1.08, 6)
    for val in eur_rows["close"]:
        assert float(val) == pytest.approx(expected, abs=1e-6)


def test_history_eurusd_zero_row_uses_default(monkeypatch):
    """A zero EURUSD close must not blow up; it must fall back to default."""
    idx = pd.date_range("2026-04-01", periods=3, freq="D")
    tickers = list(cf._YF_TICKER_MAP.keys())
    cols = pd.MultiIndex.from_product([["Close"], tickers])
    # Day 0: zero EURUSD; days 1 & 2: normal.
    data = []
    for i in range(3):
        row = [_MOCK_LAST_CLOSE[t] for t in tickers]
        if i == 0:
            row[tickers.index("EURUSD=X")] = 0.0
        data.append(row)
    frame = pd.DataFrame(data, index=idx, columns=cols)

    _patch_yf(monkeypatch, frame)
    out = cf._fetch_history_df(tickers, lookback_days=10)
    eur_closes = list(out[out["pair"] == "USD/EUR"]["close"])
    assert len(eur_closes) == 3
    # First row: default; later rows: inverted normally.
    assert eur_closes[0] == pytest.approx(cf._DEFAULTS["USD/EUR"])
    assert eur_closes[1] == pytest.approx(round(1.0 / 1.08, 6), abs=1e-6)


# ─── fetch_fx_rates (public, with isolated cache) ──────────────────────────

def test_fetch_fx_rates_miss_then_hit(monkeypatch, tmp_path):
    state = _patch_yf(monkeypatch, _make_yf_frame(_MOCK_LAST_CLOSE))
    cache = CacheManager(cache_dir=tmp_path / "cache")

    first = cf.fetch_fx_rates(cache=cache)
    assert state["calls"] == 1
    assert set(first.keys()).issubset(set(cf.KEY_CURRENCIES))
    # Every returned value is a finite, positive float.
    for pair, val in first.items():
        assert isinstance(val, float) and val > 0

    # Second call within TTL → cache hit, no new download.
    second = cf.fetch_fx_rates(cache=cache)
    assert state["calls"] == 1
    assert second == first


def test_fetch_fx_rates_falls_back_to_defaults_on_total_failure(monkeypatch, tmp_path):
    """yfinance both unavailable AND fetch path empty → fall back to defaults.

    We force yfinance off so _fetch_spot_rates_df returns the defaults frame,
    and confirm the public API returns the default dict values.
    """
    monkeypatch.setattr(cf, "_YFINANCE_AVAILABLE", False, raising=False)
    cache = CacheManager(cache_dir=tmp_path / "cache")

    out = cf.fetch_fx_rates(cache=cache)
    for pair, default in cf._DEFAULTS.items():
        assert out[pair] == pytest.approx(default)


def test_fetch_fx_rates_returns_only_known_pairs(monkeypatch, tmp_path):
    """Even if the cached DF has stray columns, they are filtered out."""
    cache = CacheManager(cache_dir=tmp_path / "cache")

    # Skip yfinance and seed the cache directly with a bogus extra column.
    seeded = dict(cf._DEFAULTS)
    seeded["BOGUS/COL"] = 99.0
    df = pd.DataFrame([seeded])
    # Mirror what CacheManager would have written.
    path = cache._path("fx", "fx_spot_rates")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=True)

    out = cf.fetch_fx_rates(cache=cache)
    assert "BOGUS/COL" not in out
    assert set(out).issubset(set(cf.KEY_CURRENCIES))


# ─── fetch_fx_history (public, with isolated cache) ────────────────────────

def test_fetch_fx_history_miss_then_hit(monkeypatch, tmp_path):
    state = _patch_yf(monkeypatch, _make_yf_frame(_MOCK_LAST_CLOSE, n_days=6))
    cache = CacheManager(cache_dir=tmp_path / "cache")

    first = cf.fetch_fx_history(lookback_days=30, cache=cache)
    assert state["calls"] == 1
    assert set(first.keys()) == set(cf.KEY_CURRENCIES)

    for pair, df in first.items():
        assert isinstance(df, pd.DataFrame)
        if not df.empty:
            assert list(df.columns) == ["date", "close", "pair"]
            assert (df["pair"] == pair).all()

    # Cache hit.
    second = cf.fetch_fx_history(lookback_days=30, cache=cache)
    assert state["calls"] == 1
    for pair in cf.KEY_CURRENCIES:
        pd.testing.assert_frame_equal(
            first[pair].reset_index(drop=True),
            second[pair].reset_index(drop=True),
        )


def test_fetch_fx_history_empty_when_fetch_fails(monkeypatch, tmp_path):
    """yfinance unavailable → public API returns empty DF per pair."""
    monkeypatch.setattr(cf, "_YFINANCE_AVAILABLE", False, raising=False)
    cache = CacheManager(cache_dir=tmp_path / "cache")

    out = cf.fetch_fx_history(lookback_days=30, cache=cache)
    assert set(out.keys()) == set(cf.KEY_CURRENCIES)
    for pair, df in out.items():
        assert isinstance(df, pd.DataFrame)
        assert df.empty


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
