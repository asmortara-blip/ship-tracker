"""Tests for ``data.stock_feed``.

The module wraps ``yfinance`` to fetch OHLCV history for a fixed roster of
shipping equities + sector ETFs, caches it on disk via ``CacheManager``, and
exposes light helpers for "latest price" and "N-day pct change". Every test
here mocks the ``yfinance`` entry point — no real Yahoo calls are made.

The ``fetch_all_stocks`` symbol is wrapped by ``st.cache_data``; tests use
its ``.__wrapped__`` form (or ``.clear()``) to bypass Streamlit's in-process
cache and exercise the underlying function deterministically. ``_fetch_single``
is wrapped by ``@retry`` from tenacity; ``.__wrapped__`` strips the retry
shell so failure-path tests don't pay 3x backoff time.

Covers:
  - _DEFAULT_TICKERS: the fixed roster shipped in the module
  - fetch_all_stocks: default tickers, custom tickers, default CacheManager,
    cache-hit reuse (fetch called once per ticker), cache key includes
    lookback_days, empty/None per-ticker frames are excluded from result,
    ttl_hours is forwarded to CacheManager, returns a dict of DataFrames
  - _fetch_single: invokes yf.Ticker(symbol).history(...) with the
    documented period/interval/auto_adjust, returns an empty DataFrame on
    raised exception, returns an empty DataFrame when yfinance returns None
    or empty, normalizes the raw response into STOCK_COLS schema
  - get_latest_price: None for missing/empty, float close[-1] otherwise
  - get_pct_change: None on missing, None on <2 rows, None on start==0,
    correct (end-start)/start otherwise, "days" window truncates history
  - fetch_all_stocks_wrapped: returns DataSeries; UNOFFICIAL source when
    populated, demo source when empty; meta carries lookback_days + tickers
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Callable

import pandas as pd
import pytest

from data import stock_feed as sf
from data.cache_manager import CacheManager
from data.normalizer import STOCK_COLS


# ─── Helpers ────────────────────────────────────────────────────────────────


def _raw_yfinance_frame(n: int = 5, start: str = "2024-01-01") -> pd.DataFrame:
    """Produce a DataFrame in the exact shape yfinance returns: capitalized
    OHLCV columns indexed by a tz-naive DatetimeIndex."""
    idx = pd.date_range(start, periods=n, freq="D")
    return pd.DataFrame(
        {
            "Open":   [100.0 + i for i in range(n)],
            "High":   [102.0 + i for i in range(n)],
            "Low":    [99.0 + i for i in range(n)],
            "Close":  [101.0 + i for i in range(n)],
            "Volume": [1000 * (i + 1) for i in range(n)],
        },
        index=idx,
    )


class _FakeTicker:
    """Drop-in stand-in for ``yf.Ticker(symbol)``.

    ``history(period, interval, auto_adjust)`` returns whatever the
    constructor was configured to return. The captured kwargs and the symbol
    are stored on a class-level list so tests can assert call shape.
    """

    calls: list[dict] = []
    factory: Callable[[str], pd.DataFrame] | None = None

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, period: str, interval: str, auto_adjust: bool) -> pd.DataFrame:
        _FakeTicker.calls.append({
            "symbol":      self.symbol,
            "period":      period,
            "interval":    interval,
            "auto_adjust": auto_adjust,
        })
        if _FakeTicker.factory is None:
            return _raw_yfinance_frame()
        return _FakeTicker.factory(self.symbol)


@pytest.fixture(autouse=True)
def _reset_fake_ticker_state():
    """Clear FakeTicker state and Streamlit's memo for fetch_all_stocks
    between tests so cache hits / call counts are deterministic."""
    _FakeTicker.calls = []
    _FakeTicker.factory = None
    try:
        sf.fetch_all_stocks.clear()
    except Exception:
        pass
    yield
    _FakeTicker.calls = []
    _FakeTicker.factory = None
    try:
        sf.fetch_all_stocks.clear()
    except Exception:
        pass


@pytest.fixture
def patched_yf(monkeypatch):
    """Replace ``stock_feed.yf`` with a namespace exposing only ``Ticker``.

    yfinance is only used via ``yf.Ticker(symbol).history(...)``, so swapping
    out the module attribute on stock_feed is sufficient and avoids any
    network access.
    """
    fake = SimpleNamespace(Ticker=_FakeTicker)
    monkeypatch.setattr(sf, "yf", fake)
    return fake


@pytest.fixture
def stock_cache(tmp_path) -> CacheManager:
    return CacheManager(tmp_path / "cache")


# Strip retry/streamlit wrappers — tests want one fast invocation per call.
_fetch_single_raw = sf._fetch_single.__wrapped__
_fetch_all_raw = sf.fetch_all_stocks.__wrapped__


# ─── _DEFAULT_TICKERS roster ────────────────────────────────────────────────


def test_default_tickers_is_the_shipping_roster() -> None:
    """The shipped roster is the contract — pin its exact shape."""
    assert sf._DEFAULT_TICKERS == [
        "ZIM", "MATX", "SBLK", "DAC", "CMRE",
        "XRT", "XLI",
    ]


def test_default_tickers_has_no_duplicates() -> None:
    assert len(sf._DEFAULT_TICKERS) == len(set(sf._DEFAULT_TICKERS))


# ─── _fetch_single ──────────────────────────────────────────────────────────


def test_fetch_single_calls_yfinance_with_correct_kwargs(patched_yf) -> None:
    """yf.Ticker(symbol).history(period=f'{N}d', interval='1d', auto_adjust=False)
    is the documented call shape; pin it. R127: auto_adjust=False keeps Close as
    the RAW price (no look-ahead back-restatement); the forward look-ahead-free
    adjustment lives in normalize_stock_df's adj_factor."""
    _fetch_single_raw("ZIM", 90)
    assert len(_FakeTicker.calls) == 1
    call = _FakeTicker.calls[0]
    assert call == {
        "symbol":      "ZIM",
        "period":      "90d",
        "interval":    "1d",
        "auto_adjust": False,
    }


def test_fetch_single_returns_normalized_stock_cols(patched_yf) -> None:
    """A successful yfinance response is normalized to STOCK_COLS schema."""
    out = _fetch_single_raw("ZIM", 5)
    assert list(out.columns) == STOCK_COLS
    assert (out["symbol"] == "ZIM").all()


def test_fetch_single_returns_empty_df_when_yfinance_raises(monkeypatch) -> None:
    """A raised exception is swallowed; an empty DataFrame is returned so
    the caller can skip the ticker rather than blow up the whole batch."""
    def _boom(symbol):
        raise RuntimeError("yahoo is down")

    monkeypatch.setattr(sf, "yf", SimpleNamespace(Ticker=_boom))
    out = _fetch_single_raw("ZIM", 30)
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_fetch_single_returns_empty_df_when_yfinance_returns_empty(patched_yf) -> None:
    """yfinance returns an empty DataFrame for delisted / unknown tickers;
    stock_feed must short-circuit and return an empty DataFrame too."""
    _FakeTicker.factory = lambda _sym: pd.DataFrame()
    out = _fetch_single_raw("GHOST", 30)
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_fetch_single_returns_empty_df_when_yfinance_returns_none(monkeypatch) -> None:
    """If a custom yfinance build returns None, the None check in
    _fetch_single must catch it (no AttributeError on .empty)."""
    class _NoneTicker:
        def __init__(self, symbol): self.symbol = symbol
        def history(self, **_kw): return None
        # match positional-arg signature too
        def __call__(self, *a, **kw): return None

    def _ticker(symbol):
        return _NoneTicker(symbol)

    monkeypatch.setattr(sf, "yf", SimpleNamespace(Ticker=_ticker))
    out = _fetch_single_raw("GHOST", 30)
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_fetch_single_forwards_symbol_into_normalized_frame(patched_yf) -> None:
    """The symbol passed in is carried through into the normalized output."""
    out = _fetch_single_raw("XRT", 5)
    assert not out.empty
    assert (out["symbol"] == "XRT").all()


# ─── fetch_all_stocks: orchestration ────────────────────────────────────────


def test_fetch_all_stocks_uses_default_tickers_when_none(
    patched_yf, stock_cache, tmp_path,
) -> None:
    """tickers=None falls back to _DEFAULT_TICKERS — every default symbol
    appears in the response keys (assuming each yields a non-empty frame)."""
    result = _fetch_all_raw(
        tickers=None, lookback_days=10, cache=stock_cache, ttl_hours=1.0,
    )
    assert set(result.keys()) == set(sf._DEFAULT_TICKERS)


def test_fetch_all_stocks_custom_tickers(patched_yf, stock_cache) -> None:
    """A caller-supplied roster overrides the default exactly."""
    result = _fetch_all_raw(
        tickers=["AAA", "BBB"], lookback_days=30, cache=stock_cache, ttl_hours=1.0,
    )
    assert set(result.keys()) == {"AAA", "BBB"}


def test_fetch_all_stocks_returns_dataframes(patched_yf, stock_cache) -> None:
    """Every value in the result dict is a DataFrame with STOCK_COLS columns."""
    result = _fetch_all_raw(
        tickers=["AAA"], lookback_days=10, cache=stock_cache, ttl_hours=1.0,
    )
    df = result["AAA"]
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == STOCK_COLS


def test_fetch_all_stocks_default_cache_is_created_when_none(
    patched_yf, monkeypatch, tmp_path,
) -> None:
    """cache=None triggers ``CacheManager()`` construction. Verify by
    redirecting that default constructor at a tmp path so we don't pollute
    the repo's cache/ directory."""
    seen = {"made": 0}
    real_init = CacheManager.__init__

    def _spy_init(self, cache_dir="cache"):
        seen["made"] += 1
        real_init(self, tmp_path / "cache")

    monkeypatch.setattr(CacheManager, "__init__", _spy_init)
    _fetch_all_raw(tickers=["AAA"], lookback_days=10, cache=None, ttl_hours=1.0)
    assert seen["made"] >= 1


def test_fetch_all_stocks_excludes_tickers_with_empty_data(
    patched_yf, stock_cache,
) -> None:
    """A ticker that yfinance can't resolve must NOT appear in the dict —
    no None placeholders, no empty-DataFrame rows."""
    def _factory(symbol):
        return pd.DataFrame() if symbol == "BAD" else _raw_yfinance_frame()

    _FakeTicker.factory = _factory
    result = _fetch_all_raw(
        tickers=["AAA", "BAD", "BBB"], lookback_days=10,
        cache=stock_cache, ttl_hours=1.0,
    )
    assert "BAD" not in result
    assert set(result.keys()) == {"AAA", "BBB"}


def test_fetch_all_stocks_cache_key_includes_lookback_days(
    patched_yf, stock_cache,
) -> None:
    """Cache key is ``f'{symbol}_{lookback_days}d'`` — pin it by checking
    the slugified Parquet filename CacheManager creates."""
    _fetch_all_raw(
        tickers=["ZIM"], lookback_days=42, cache=stock_cache, ttl_hours=1.0,
    )
    cache_root = stock_cache.cache_dir
    expected = cache_root / "stocks" / "zim_42d.parquet"
    assert expected.exists()


def test_fetch_all_stocks_writes_to_stocks_subdir(patched_yf, stock_cache) -> None:
    """The CacheManager source label is 'stocks' — that becomes the subdir."""
    _fetch_all_raw(
        tickers=["ZIM"], lookback_days=10, cache=stock_cache, ttl_hours=1.0,
    )
    stocks_dir = stock_cache.cache_dir / "stocks"
    assert stocks_dir.is_dir()
    assert list(stocks_dir.glob("*.parquet"))


def test_fetch_all_stocks_second_call_hits_cache(patched_yf, stock_cache) -> None:
    """Second invocation inside TTL doesn't re-call yfinance."""
    _fetch_all_raw(
        tickers=["ZIM"], lookback_days=10, cache=stock_cache, ttl_hours=1.0,
    )
    n_first = len(_FakeTicker.calls)
    _fetch_all_raw(
        tickers=["ZIM"], lookback_days=10, cache=stock_cache, ttl_hours=1.0,
    )
    assert len(_FakeTicker.calls) == n_first  # no additional yfinance hits


def test_fetch_all_stocks_distinct_lookbacks_use_distinct_cache_entries(
    patched_yf, stock_cache,
) -> None:
    """Different lookback_days produce different cache keys → both written."""
    _fetch_all_raw(
        tickers=["ZIM"], lookback_days=30, cache=stock_cache, ttl_hours=1.0,
    )
    _fetch_all_raw(
        tickers=["ZIM"], lookback_days=90, cache=stock_cache, ttl_hours=1.0,
    )
    stocks_dir = stock_cache.cache_dir / "stocks"
    names = {p.name for p in stocks_dir.glob("*.parquet")}
    assert "zim_30d.parquet" in names
    assert "zim_90d.parquet" in names


def test_fetch_all_stocks_lookback_days_propagates_to_yfinance(
    patched_yf, stock_cache,
) -> None:
    """The ``lookback_days`` parameter ends up in yfinance's ``period`` kwarg
    as ``f'{N}d'``."""
    _fetch_all_raw(
        tickers=["ZIM"], lookback_days=77, cache=stock_cache, ttl_hours=1.0,
    )
    periods = {c["period"] for c in _FakeTicker.calls}
    assert periods == {"77d"}


# ─── get_latest_price ───────────────────────────────────────────────────────


def test_get_latest_price_returns_last_close() -> None:
    df = pd.DataFrame({"close": [100.0, 101.5, 103.25]})
    out = sf.get_latest_price("ZIM", {"ZIM": df})
    assert isinstance(out, float)
    assert out == 103.25


def test_get_latest_price_none_for_missing_symbol() -> None:
    assert sf.get_latest_price("ZIM", {}) is None


def test_get_latest_price_none_for_empty_dataframe() -> None:
    assert sf.get_latest_price("ZIM", {"ZIM": pd.DataFrame()}) is None


def test_get_latest_price_returns_python_float_not_numpy() -> None:
    """The cast to ``float(...)`` is explicit — pin it so downstream JSON /
    Streamlit consumers don't get a numpy scalar."""
    df = pd.DataFrame({"close": [100.0, 105.0]})
    out = sf.get_latest_price("ZIM", {"ZIM": df})
    assert type(out) is float


# ─── get_pct_change ─────────────────────────────────────────────────────────


def test_get_pct_change_returns_correct_ratio() -> None:
    """((end - start) / start) over the window."""
    df = pd.DataFrame({"close": [100.0, 105.0, 110.0]})
    out = sf.get_pct_change("ZIM", {"ZIM": df}, days=2)
    # tail(days+1)=tail(3) → start=100, end=110, change=0.10
    assert out == pytest.approx(0.10)


def test_get_pct_change_truncates_to_days_window() -> None:
    """The window is the trailing ``days + 1`` rows, ignoring older history."""
    df = pd.DataFrame({"close": [10.0, 20.0, 80.0, 90.0, 100.0]})
    # days=2 → tail(3) = [80, 90, 100] → (100-80)/80 = 0.25
    out = sf.get_pct_change("ZIM", {"ZIM": df}, days=2)
    assert out == pytest.approx(0.25)


def test_get_pct_change_none_for_missing_symbol() -> None:
    assert sf.get_pct_change("ZIM", {}, days=30) is None


def test_get_pct_change_none_for_single_row() -> None:
    """Need at least 2 rows to compute a delta."""
    df = pd.DataFrame({"close": [100.0]})
    assert sf.get_pct_change("ZIM", {"ZIM": df}, days=30) is None


def test_get_pct_change_none_when_start_price_is_zero() -> None:
    """Guard against division by zero."""
    df = pd.DataFrame({"close": [0.0, 100.0]})
    assert sf.get_pct_change("ZIM", {"ZIM": df}, days=30) is None


def test_get_pct_change_handles_negative_change() -> None:
    df = pd.DataFrame({"close": [100.0, 90.0]})
    out = sf.get_pct_change("ZIM", {"ZIM": df}, days=30)
    assert out == pytest.approx(-0.10)


# ─── fetch_all_stocks_wrapped (DataSeries variant) ──────────────────────────


def test_wrapped_returns_dataseries_with_unofficial_quality(
    patched_yf, stock_cache, monkeypatch,
) -> None:
    """When data arrives, the DataSeries source is the live Yahoo descriptor
    with quality=UNOFFICIAL."""
    from data.quality import DataKind, DataQuality

    # Make sf.fetch_all_stocks call return a populated dict without going
    # through Streamlit's cache layer. We bypass the wrapper by patching
    # the module-level symbol with the unwrapped function bound to our
    # cache + fake-yf via the monkeypatch already in place.
    def _inner(tickers=None, lookback_days=180, cache=None, ttl_hours=1.0):
        return _fetch_all_raw(
            tickers=tickers, lookback_days=lookback_days,
            cache=stock_cache, ttl_hours=ttl_hours,
        )

    monkeypatch.setattr(sf, "fetch_all_stocks", _inner)
    series = sf.fetch_all_stocks_wrapped(
        tickers=["AAA", "BBB"], lookback_days=30, cache=stock_cache, ttl_hours=2.5,
    )
    assert series.source.name == "Yahoo Finance"
    assert series.source.kind == DataKind.SCRAPED
    assert series.source.quality == DataQuality.UNOFFICIAL
    assert series.source.sla_hours == 2.5
    assert set(series.data.keys()) == {"AAA", "BBB"}
    assert series.meta["lookback_days"] == 30
    assert series.meta["tickers"] == ["AAA", "BBB"]


def test_wrapped_returns_demo_source_when_empty(
    patched_yf, stock_cache, monkeypatch,
) -> None:
    """When fetch_all_stocks yields no data, the wrapper hands back a demo
    DataSource so the UI knows to surface a placeholder."""
    from data.quality import DataQuality

    def _empty(**_kw):
        return {}

    monkeypatch.setattr(sf, "fetch_all_stocks", _empty)
    series = sf.fetch_all_stocks_wrapped(
        tickers=["AAA"], lookback_days=10, cache=stock_cache, ttl_hours=1.0,
    )
    assert series.data == {}
    assert series.source.quality == DataQuality.DEMO


def test_wrapped_meta_defaults_to_default_tickers_when_none(
    patched_yf, stock_cache, monkeypatch,
) -> None:
    """meta['tickers'] reflects the default roster when tickers=None."""
    monkeypatch.setattr(sf, "fetch_all_stocks", lambda **_kw: {})
    series = sf.fetch_all_stocks_wrapped(
        tickers=None, lookback_days=60, cache=stock_cache, ttl_hours=1.0,
    )
    assert series.meta["tickers"] == sf._DEFAULT_TICKERS
    assert series.meta["lookback_days"] == 60


# ── deepen_stock_cache (deep-history persistence for the VaR backtest) ────────

# Conftest no-ops data.stock_feed.deepen_stock_cache by default (so the scheduler
# job never fetches yfinance in tests); capture the REAL function at import to
# exercise it directly here.
_deepen_raw = sf.deepen_stock_cache


def test_deepen_stock_cache_writes_deep_parquet(tmp_path):
    def fake_fetch(sym, lookback):
        assert lookback == 1825
        return pd.DataFrame({"date": pd.date_range("2021-01-01", periods=5),
                             "symbol": [sym] * 5, "close": range(5)})

    res = _deepen_raw(["ZIM", "MATX"], lookback_days=1825,
                      cache_dir=str(tmp_path), inter_request_sleep=0,
                      fetch=fake_fetch)
    assert res == {"ZIM": "written", "MATX": "written"}
    assert (tmp_path / "stocks" / "zim_1825d.parquet").exists()
    assert (tmp_path / "stocks" / "matx_1825d.parquet").exists()


def test_deepen_stock_cache_skips_empty_and_never_raises(tmp_path):
    def empty_fetch(sym, lookback):
        return pd.DataFrame()

    def raising_fetch(sym, lookback):
        raise ConnectionError("throttled")

    assert _deepen_raw(["ZIM"], cache_dir=str(tmp_path),
                       inter_request_sleep=0, fetch=empty_fetch) == {"ZIM": "skipped"}
    assert _deepen_raw(["ZIM"], cache_dir=str(tmp_path),
                       inter_request_sleep=0, fetch=raising_fetch) == {"ZIM": "skipped"}
    # A skipped (throttled/empty) fetch must NOT write a parquet — silence never
    # overwrites real cached data.
    sdir = tmp_path / "stocks"
    assert not sdir.exists() or not list(sdir.glob("*.parquet"))
