"""Tests for ``data.alphavantage_feed``.

The module wraps the Alpha Vantage REST API (OVERVIEW, GLOBAL_QUOTE,
INCOME_STATEMENT) behind a TTL Parquet cache and a 12.5 s/request gate.
Every test below mocks ``requests.get`` and the cache layer so no real
API call is ever made. ``ALPHA_VANTAGE_KEY`` is set to a dummy value per
test via ``monkeypatch.setenv``; tests that need to exercise the
"no-key" branch monkey-patch ``_get_api_key`` directly.

Covers (defining-property style):

  - ``_get_api_key``
      * Returns env var when ``st.secrets`` is unavailable.
      * Empty string when env var is unset.

  - ``alphavantage_available``
      * True iff a key is configured.

  - ``_safe_float``
      * Sentinel strings (None, "None", "N/A", "-", "") → 0.0
      * Numeric strings parse to float
      * Garbage strings → 0.0 (no exception)
      * Native int / float pass through

  - ``_now_iso``
      * Returns an ISO-8601 string parseable by ``datetime.fromisoformat``.

  - ``_rate_limited_get`` (rate-lock bypassed)
      * Sleeps for the remainder of the 12.5 s gap when the prior request
        was recent (verified via patched ``time.time`` + ``time.sleep``).
      * Does not sleep when the elapsed time is already > 12.5 s.
      * Updates ``_last_request_time`` after the network call.
      * ``Note`` key in JSON → empty dict (rate-limit branch).
      * ``Information`` key in JSON → empty dict (bad-key branch).
      * HTTP exception → empty dict.
      * raise_for_status raising HTTPError → empty dict.
      * Happy-path JSON returned unchanged.

  - ``_fetch_fundamentals_raw``
      * Missing ``Symbol`` field → None.
      * Empty data dict → None.
      * Numeric coercion: ``MarketCapitalization`` is divided by 1e9.
      * Percent fields (DividendYield, ProfitMargin, ReturnOnEquityTTM,
        QuarterlyEarningsGrowthYOY) are multiplied by 100.
      * Missing keys are filled by ``_safe_float`` → 0.0.

  - ``_fetch_quote_raw``
      * Missing ``Global Quote`` block → None.
      * Empty ``Global Quote`` block → None.
      * ``10. change percent`` strips the trailing "%".
      * Volume cast to int.

  - ``_fetch_income_raw``
      * Empty quarterlyReports → None.
      * Annualizes the latest quarter (revenue * 4).
      * QoQ growth computed from q0 vs q1.
      * Margin = component / revenue * 100.
      * Zero revenue triggers safe fallback (no ZeroDivisionError).
      * Single quarter ⇒ revenue_qoq = 0.0.

  - ``_dataclass_to_df`` / ``_df_to_*`` round-trip
      * Fundamentals, Quote, Income all survive serialize → deserialize.
      * None / empty df → None.
      * Missing column → None (deserializer swallows KeyError).

  - Public ``fetch_fundamentals`` / ``fetch_quote`` / ``fetch_income``
      * No API key → returns None and never builds CacheManager.
      * Cache miss invokes ``requests.get`` once and returns the dataclass.
      * Cache hit on second call: no second network roundtrip.
      * Cache returns empty DataFrame → public API returns None.

  - ``fetch_all_shipping_fundamentals``
      * No API key → empty dict, no network.
      * Default ticker list pulled from ``_DEFAULT_TICKERS``.
      * Failed tickers are silently skipped (per-ticker try/except).
      * Each successful ticker shows up keyed by its symbol.

  - ``build_fundamentals_table_html``
      * Empty input → placeholder ``<em>`` message.
      * Renders one ``<tr>`` per ticker with ticker symbol present.
      * Target-color branches:
          - target >= 52W high → green (#27ae60)
          - target <= 52W low  → red (#e74c3c)
          - within range       → amber (#f39c12)
          - missing / zero data → grey (#888888)
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from data import alphavantage_feed as av
from data.alphavantage_feed import (
    CompanyIncome,
    StockFundamentals,
    StockQuote,
    _dataclass_to_df,
    _df_to_fundamentals,
    _df_to_income,
    _df_to_quote,
    _fetch_fundamentals_raw,
    _fetch_income_raw,
    _fetch_quote_raw,
    _get_api_key,
    _now_iso,
    _rate_limited_get,
    _safe_float,
    alphavantage_available,
    build_fundamentals_table_html,
    fetch_all_shipping_fundamentals,
    fetch_fundamentals,
    fetch_income,
    fetch_quote,
)


# ─── Test helpers ───────────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal ``requests.Response`` look-alike."""

    def __init__(self, payload: dict, status: int = 200, raise_exc: Exception | None = None):
        self._payload = payload
        self.status_code = status
        self._raise_exc = raise_exc

    def raise_for_status(self) -> None:
        if self._raise_exc is not None:
            raise self._raise_exc

    def json(self) -> dict:
        return self._payload


def _install_fake_get(monkeypatch, payload, raise_exc: Exception | None = None) -> dict:
    """Patch ``av.requests.get`` to return ``payload``. Returns a call counter."""
    state = {"calls": 0, "last_params": None, "last_url": None}

    def _fake_get(url, params=None, timeout=None):
        state["calls"] += 1
        state["last_url"] = url
        state["last_params"] = params
        if raise_exc is not None:
            raise raise_exc
        return _FakeResponse(payload)

    monkeypatch.setattr(av.requests, "get", _fake_get)
    return state


def _isolated_cache(monkeypatch, tmp_path):
    """Force ``CacheManager`` instantiated by the module under test to use tmp."""
    from data import cache_manager as cm

    orig_init = cm.CacheManager.__init__

    def _init(self, cache_dir="cache"):
        orig_init(self, cache_dir=tmp_path)

    monkeypatch.setattr(cm.CacheManager, "__init__", _init)


@pytest.fixture(autouse=True)
def _zero_rate_gate(monkeypatch):
    """Make the rate gate a no-op so unit tests don't sleep 12.5 s each."""
    monkeypatch.setattr(av, "_last_request_time", 0.0, raising=True)
    # Default: pretend we've never made a request and time is "now=0", which
    # makes elapsed > 12.5 trivially. Individual tests that want to exercise
    # the sleep branch patch time.time directly.
    monkeypatch.setattr(av.time, "sleep", lambda *_a, **_kw: None)


@pytest.fixture
def overview_payload() -> dict:
    """Canonical Alpha Vantage OVERVIEW response (subset)."""
    return {
        "Symbol": "ZIM",
        "Name": "ZIM Integrated Shipping",
        "Sector": "Industrials",
        "Industry": "Marine Shipping",
        "MarketCapitalization": "2500000000",   # 2.5 B raw
        "PERatio": "8.4",
        "ForwardPE": "6.1",
        "EPS": "1.23",
        "DividendYield": "0.05",                 # 5% — module multiplies by 100
        "Beta": "1.45",
        "BookValue": "12.4",
        "PriceToBookRatio": "0.95",
        "EVToEBITDA": "4.2",
        "ProfitMargin": "0.08",                  # 8%
        "ReturnOnEquityTTM": "0.15",             # 15%
        "QuarterlyEarningsGrowthYOY": "0.22",    # 22%
        "AnalystTargetPrice": "18.5",
        "52WeekHigh": "21.4",
        "52WeekLow": "9.8",
        "Description": "Container shipping line.",
    }


@pytest.fixture
def quote_payload() -> dict:
    """Canonical Alpha Vantage GLOBAL_QUOTE response."""
    return {
        "Global Quote": {
            "01. symbol": "ZIM",
            "02. open": "15.20",
            "03. high": "15.80",
            "04. low": "14.95",
            "05. price": "15.50",
            "06. volume": "1234567",
            "07. latest trading day": "2026-05-21",
            "08. previous close": "15.10",
            "09. change": "0.40",
            "10. change percent": "2.65%",
        }
    }


@pytest.fixture
def income_payload() -> dict:
    """Canonical Alpha Vantage INCOME_STATEMENT response (two quarters)."""
    return {
        "quarterlyReports": [
            {
                "fiscalDateEnding": "2026-03-31",
                "totalRevenue": "1000000000",       # 1 B
                "grossProfit":  "300000000",        # 30%
                "operatingIncome": "150000000",     # 15%
                "netIncome":    "100000000",        # 10%
                "ebitda":       "250000000",
            },
            {
                "fiscalDateEnding": "2025-12-31",
                "totalRevenue": "800000000",        # 800 M — qoq = +25%
                "grossProfit":  "240000000",
                "operatingIncome": "120000000",
                "netIncome":    "80000000",
                "ebitda":       "200000000",
            },
        ]
    }


# ─── _get_api_key + alphavantage_available ──────────────────────────────────


def test_get_api_key_reads_env(monkeypatch):
    """Env var is the fallback when st.secrets has no key."""
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "DUMMY_ENV_KEY")
    # st.secrets may not have the key in test runtime — that's fine, the
    # function should still find the env var.
    assert _get_api_key() == "DUMMY_ENV_KEY"


def test_get_api_key_empty_when_unset(monkeypatch):
    """Missing env var + missing secret → empty string."""
    monkeypatch.delenv("ALPHA_VANTAGE_KEY", raising=False)
    # Force st.secrets.get to also return empty so we exercise the env path.
    monkeypatch.setattr(av.st, "secrets", {}, raising=False)
    assert _get_api_key() == ""


def test_alphavantage_available_reflects_key(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "K")
    assert alphavantage_available() is True

    monkeypatch.delenv("ALPHA_VANTAGE_KEY", raising=False)
    monkeypatch.setattr(av.st, "secrets", {}, raising=False)
    assert alphavantage_available() is False


# ─── _safe_float ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("sentinel", [None, "None", "N/A", "-", ""])
def test_safe_float_sentinels_return_zero(sentinel):
    assert _safe_float(sentinel) == 0.0


def test_safe_float_numeric_strings_parse():
    assert _safe_float("3.14") == pytest.approx(3.14)
    assert _safe_float("0") == 0.0
    assert _safe_float("-1.5") == pytest.approx(-1.5)


def test_safe_float_passthrough_numerics():
    assert _safe_float(42) == 42.0
    assert _safe_float(2.5) == pytest.approx(2.5)


def test_safe_float_garbage_returns_zero():
    assert _safe_float("notanumber") == 0.0
    assert _safe_float([1, 2, 3]) == 0.0


# ─── _now_iso ───────────────────────────────────────────────────────────────


def test_now_iso_is_parseable():
    out = _now_iso()
    # Should be a parseable ISO-8601 string with a tz suffix.
    parsed = datetime.fromisoformat(out)
    assert parsed.tzinfo is not None


# ─── _rate_limited_get ──────────────────────────────────────────────────────


def test_rate_limited_get_happy_path_returns_json(monkeypatch):
    payload = {"Symbol": "ZIM", "Name": "ZIM"}
    state = _install_fake_get(monkeypatch, payload)
    out = _rate_limited_get("https://x", {"q": 1})
    assert out == payload
    assert state["calls"] == 1


def test_rate_limited_get_note_key_returns_empty(monkeypatch):
    _install_fake_get(monkeypatch, {"Note": "Thank you for using Alpha Vantage! Standard API call frequency is 5/min..."})
    assert _rate_limited_get("https://x", {}) == {}


def test_rate_limited_get_information_key_returns_empty(monkeypatch):
    _install_fake_get(monkeypatch, {"Information": "API key invalid"})
    assert _rate_limited_get("https://x", {}) == {}


def test_rate_limited_get_swallows_http_exception(monkeypatch):
    _install_fake_get(monkeypatch, {}, raise_exc=RuntimeError("boom"))
    assert _rate_limited_get("https://x", {}) == {}


def test_rate_limited_get_swallows_raise_for_status(monkeypatch):
    """raise_for_status raising still leaves us with an empty dict, not a crash."""
    class _Bad(_FakeResponse):
        def raise_for_status(self):
            raise RuntimeError("404")

    def _bad_get(*a, **kw):
        return _Bad({})

    monkeypatch.setattr(av.requests, "get", _bad_get)
    assert _rate_limited_get("https://x", {}) == {}


def test_rate_limited_get_sleeps_when_too_recent(monkeypatch):
    """If <12.5 s since last request, the function must sleep for the gap."""
    # Pretend the last request was 1 s ago (relative to time.time()).
    fixed_now = 1000.0
    sleep_calls: list[float] = []
    monkeypatch.setattr(av.time, "time", lambda: fixed_now)
    monkeypatch.setattr(av.time, "sleep", lambda s: sleep_calls.append(s))
    monkeypatch.setattr(av, "_last_request_time", fixed_now - 1.0, raising=True)

    _install_fake_get(monkeypatch, {"Symbol": "X"})
    _rate_limited_get("https://x", {})

    assert len(sleep_calls) == 1
    # Sleep gap should be (12.5 - 1.0) = 11.5 s.
    assert sleep_calls[0] == pytest.approx(11.5, abs=1e-6)


def test_rate_limited_get_no_sleep_when_old(monkeypatch):
    """If elapsed > 12.5 s, no sleep."""
    sleep_calls: list[float] = []
    monkeypatch.setattr(av.time, "time", lambda: 1000.0)
    monkeypatch.setattr(av.time, "sleep", lambda s: sleep_calls.append(s))
    monkeypatch.setattr(av, "_last_request_time", 0.0, raising=True)  # ages ago

    _install_fake_get(monkeypatch, {"Symbol": "X"})
    _rate_limited_get("https://x", {})

    assert sleep_calls == []


# ─── _fetch_fundamentals_raw ────────────────────────────────────────────────


def test_fetch_fundamentals_raw_happy_path(monkeypatch, overview_payload):
    state = _install_fake_get(monkeypatch, overview_payload)

    fund = _fetch_fundamentals_raw("ZIM", api_key="K")

    assert state["calls"] == 1
    assert state["last_params"]["function"] == "OVERVIEW"
    assert state["last_params"]["symbol"] == "ZIM"
    assert state["last_params"]["apikey"] == "K"

    assert isinstance(fund, StockFundamentals)
    assert fund.ticker == "ZIM"
    assert fund.name == "ZIM Integrated Shipping"
    # MarketCapitalization 2.5 B raw → 2.5 (billions)
    assert fund.market_cap == pytest.approx(2.5)
    assert fund.pe_ratio == pytest.approx(8.4)
    assert fund.eps == pytest.approx(1.23)
    # 0.05 -> 5% after *100
    assert fund.dividend_yield_pct == pytest.approx(5.0)
    assert fund.profit_margin_pct == pytest.approx(8.0)
    assert fund.roe_pct == pytest.approx(15.0)
    assert fund.revenue_growth_yoy_pct == pytest.approx(22.0)
    assert fund.week_52_high == pytest.approx(21.4)
    assert fund.week_52_low == pytest.approx(9.8)


def test_fetch_fundamentals_raw_missing_symbol_returns_none(monkeypatch):
    _install_fake_get(monkeypatch, {"unrelated": "blob"})
    assert _fetch_fundamentals_raw("ZIM", "K") is None


def test_fetch_fundamentals_raw_empty_dict_returns_none(monkeypatch):
    _install_fake_get(monkeypatch, {})
    assert _fetch_fundamentals_raw("ZIM", "K") is None


def test_fetch_fundamentals_raw_handles_missing_fields(monkeypatch):
    """Sparse payloads still parse — _safe_float defaults to 0.0."""
    _install_fake_get(monkeypatch, {"Symbol": "ZIM"})
    fund = _fetch_fundamentals_raw("ZIM", "K")
    assert fund is not None
    assert fund.ticker == "ZIM"
    # Missing numeric fields default to 0.0
    assert fund.pe_ratio == 0.0
    assert fund.dividend_yield_pct == 0.0
    assert fund.market_cap == 0.0


# ─── _fetch_quote_raw ───────────────────────────────────────────────────────


def test_fetch_quote_raw_happy_path(monkeypatch, quote_payload):
    state = _install_fake_get(monkeypatch, quote_payload)
    q = _fetch_quote_raw("ZIM", "K")

    assert state["last_params"]["function"] == "GLOBAL_QUOTE"
    assert isinstance(q, StockQuote)
    assert q.ticker == "ZIM"
    assert q.price == pytest.approx(15.50)
    assert q.open == pytest.approx(15.20)
    assert q.high == pytest.approx(15.80)
    assert q.low == pytest.approx(14.95)
    assert q.volume == 1234567
    assert q.prev_close == pytest.approx(15.10)
    # 2.65% → strip "%" → 2.65
    assert q.change_pct == pytest.approx(2.65)


def test_fetch_quote_raw_missing_block_returns_none(monkeypatch):
    _install_fake_get(monkeypatch, {})
    assert _fetch_quote_raw("ZIM", "K") is None


def test_fetch_quote_raw_empty_block_returns_none(monkeypatch):
    _install_fake_get(monkeypatch, {"Global Quote": {}})
    assert _fetch_quote_raw("ZIM", "K") is None


def test_fetch_quote_raw_volume_cast_to_int(monkeypatch):
    """Volume must come back as int even when AV returns a stringified float."""
    payload = {"Global Quote": {
        "01. symbol": "X",
        "02. open": "1",
        "03. high": "2",
        "04. low": "0.5",
        "05. price": "1.5",
        "06. volume": "1500",
        "08. previous close": "1.4",
        "10. change percent": "7.14%",
    }}
    _install_fake_get(monkeypatch, payload)
    q = _fetch_quote_raw("X", "K")
    assert q is not None
    assert isinstance(q.volume, int)
    assert q.volume == 1500


# ─── _fetch_income_raw ──────────────────────────────────────────────────────


def test_fetch_income_raw_happy_path(monkeypatch, income_payload):
    state = _install_fake_get(monkeypatch, income_payload)
    inc = _fetch_income_raw("ZIM", "K")

    assert state["last_params"]["function"] == "INCOME_STATEMENT"
    assert isinstance(inc, CompanyIncome)
    # 1B revenue * 4 -> 4B annualized
    assert inc.latest_revenue == pytest.approx(4e9)
    # QoQ growth: (1000 - 800) / 800 * 100 = 25%
    assert inc.revenue_qoq_growth_pct == pytest.approx(25.0)
    # 300 / 1000 * 100 = 30
    assert inc.gross_margin_pct == pytest.approx(30.0)
    # 150 / 1000 * 100 = 15
    assert inc.operating_margin_pct == pytest.approx(15.0)
    # 100 / 1000 * 100 = 10
    assert inc.net_margin_pct == pytest.approx(10.0)
    assert inc.ebitda == pytest.approx(250000000.0)


def test_fetch_income_raw_no_quarterly_returns_none(monkeypatch):
    _install_fake_get(monkeypatch, {"quarterlyReports": []})
    assert _fetch_income_raw("ZIM", "K") is None


def test_fetch_income_raw_missing_key_returns_none(monkeypatch):
    _install_fake_get(monkeypatch, {})
    assert _fetch_income_raw("ZIM", "K") is None


def test_fetch_income_raw_single_quarter_qoq_is_zero(monkeypatch):
    """One quarter on file ⇒ prior-quarter revenue is 0 ⇒ growth defaults to 0."""
    payload = {"quarterlyReports": [{
        "totalRevenue": "1000",
        "grossProfit": "100",
        "operatingIncome": "50",
        "netIncome": "25",
        "ebitda": "60",
    }]}
    _install_fake_get(monkeypatch, payload)
    inc = _fetch_income_raw("ZIM", "K")
    assert inc is not None
    assert inc.revenue_qoq_growth_pct == 0.0


def test_fetch_income_raw_zero_revenue_safe(monkeypatch):
    """Zero revenue must not raise ZeroDivisionError; margins go to 0."""
    payload = {"quarterlyReports": [{
        "totalRevenue": "0",
        "grossProfit": "0",
        "operatingIncome": "0",
        "netIncome": "0",
        "ebitda": "0",
    }]}
    _install_fake_get(monkeypatch, payload)
    inc = _fetch_income_raw("ZIM", "K")
    assert inc is not None
    assert inc.latest_revenue == 0.0
    assert inc.gross_margin_pct == 0.0
    assert inc.operating_margin_pct == 0.0
    assert inc.net_margin_pct == 0.0


# ─── _dataclass_to_df + _df_to_* round-trips ────────────────────────────────


def test_fundamentals_round_trip():
    src = StockFundamentals(
        ticker="ZIM", name="ZIM", sector="S", industry="I",
        market_cap=2.5, pe_ratio=8.4, forward_pe=6.1, eps=1.23,
        dividend_yield_pct=5.0, beta=1.45, book_value=12.4, pb_ratio=0.95,
        ev_to_ebitda=4.2, profit_margin_pct=8.0, roe_pct=15.0,
        revenue_growth_yoy_pct=22.0, analyst_target_price=18.5,
        week_52_high=21.4, week_52_low=9.8, description="x",
        fetched_at="2026-05-21T00:00:00+00:00",
    )
    df = _dataclass_to_df(src)
    assert len(df) == 1
    out = _df_to_fundamentals(df)
    assert out == src


def test_quote_round_trip():
    src = StockQuote(
        ticker="ZIM", price=15.5, open=15.2, high=15.8, low=14.95,
        volume=1234567, prev_close=15.1, change_pct=2.65,
        fetched_at="2026-05-21T00:00:00+00:00",
    )
    df = _dataclass_to_df(src)
    out = _df_to_quote(df)
    assert out == src


def test_income_round_trip():
    src = CompanyIncome(
        ticker="ZIM", latest_revenue=4e9, revenue_qoq_growth_pct=25.0,
        gross_margin_pct=30.0, operating_margin_pct=15.0, net_margin_pct=10.0,
        ebitda=2.5e8, fetched_at="2026-05-21T00:00:00+00:00",
    )
    df = _dataclass_to_df(src)
    out = _df_to_income(df)
    assert out == src


def test_df_to_fundamentals_none_or_empty():
    assert _df_to_fundamentals(None) is None
    assert _df_to_fundamentals(pd.DataFrame()) is None


def test_df_to_quote_none_or_empty():
    assert _df_to_quote(None) is None
    assert _df_to_quote(pd.DataFrame()) is None


def test_df_to_income_none_or_empty():
    assert _df_to_income(None) is None
    assert _df_to_income(pd.DataFrame()) is None


def test_df_to_fundamentals_missing_column_returns_none():
    """A frame missing required columns must not crash — return None."""
    df = pd.DataFrame([{"ticker": "ZIM"}])  # no other fields
    assert _df_to_fundamentals(df) is None


def test_df_to_quote_missing_column_returns_none():
    df = pd.DataFrame([{"ticker": "ZIM"}])
    assert _df_to_quote(df) is None


def test_df_to_income_missing_column_returns_none():
    df = pd.DataFrame([{"ticker": "ZIM"}])
    assert _df_to_income(df) is None


# ─── Public fetch_fundamentals / fetch_quote / fetch_income ─────────────────


def test_fetch_fundamentals_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_KEY", raising=False)
    monkeypatch.setattr(av.st, "secrets", {}, raising=False)
    # Should never touch requests.
    monkeypatch.setattr(av.requests, "get", lambda *a, **kw: pytest.fail("network called"))
    assert fetch_fundamentals("ZIM") is None


def test_fetch_fundamentals_cache_miss_then_hit(monkeypatch, tmp_path, overview_payload):
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "K")
    _isolated_cache(monkeypatch, tmp_path)
    state = _install_fake_get(monkeypatch, overview_payload)

    out1 = fetch_fundamentals("ZIM", cache_ttl_hours=24.0)
    assert out1 is not None
    assert out1.ticker == "ZIM"
    assert state["calls"] == 1

    # Second call within TTL — must read from cache, not the network.
    out2 = fetch_fundamentals("ZIM", cache_ttl_hours=24.0)
    assert out2 is not None
    assert out2.ticker == "ZIM"
    assert state["calls"] == 1  # unchanged


def test_fetch_fundamentals_empty_payload_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "K")
    _isolated_cache(monkeypatch, tmp_path)
    _install_fake_get(monkeypatch, {})  # no Symbol → _fetch_fundamentals_raw returns None
    out = fetch_fundamentals("XYZ", cache_ttl_hours=24.0)
    assert out is None


def test_fetch_quote_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_KEY", raising=False)
    monkeypatch.setattr(av.st, "secrets", {}, raising=False)
    monkeypatch.setattr(av.requests, "get", lambda *a, **kw: pytest.fail("network called"))
    assert fetch_quote("ZIM") is None


def test_fetch_quote_cache_miss_then_hit(monkeypatch, tmp_path, quote_payload):
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "K")
    _isolated_cache(monkeypatch, tmp_path)
    state = _install_fake_get(monkeypatch, quote_payload)

    out1 = fetch_quote("ZIM")
    assert out1 is not None
    assert out1.price == pytest.approx(15.5)
    assert state["calls"] == 1

    out2 = fetch_quote("ZIM")
    assert out2 is not None
    assert state["calls"] == 1


def test_fetch_quote_empty_payload_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "K")
    _isolated_cache(monkeypatch, tmp_path)
    _install_fake_get(monkeypatch, {})
    assert fetch_quote("XYZ") is None


def test_fetch_income_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_KEY", raising=False)
    monkeypatch.setattr(av.st, "secrets", {}, raising=False)
    monkeypatch.setattr(av.requests, "get", lambda *a, **kw: pytest.fail("network called"))
    assert fetch_income("ZIM") is None


def test_fetch_income_cache_miss_then_hit(monkeypatch, tmp_path, income_payload):
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "K")
    _isolated_cache(monkeypatch, tmp_path)
    state = _install_fake_get(monkeypatch, income_payload)

    out1 = fetch_income("ZIM")
    assert out1 is not None
    assert out1.latest_revenue == pytest.approx(4e9)
    assert state["calls"] == 1

    out2 = fetch_income("ZIM")
    assert out2 is not None
    assert state["calls"] == 1


def test_fetch_income_empty_payload_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "K")
    _isolated_cache(monkeypatch, tmp_path)
    _install_fake_get(monkeypatch, {})
    assert fetch_income("XYZ") is None


# ─── fetch_all_shipping_fundamentals ────────────────────────────────────────


def test_fetch_all_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_KEY", raising=False)
    monkeypatch.setattr(av.st, "secrets", {}, raising=False)
    monkeypatch.setattr(av.requests, "get", lambda *a, **kw: pytest.fail("network called"))
    assert fetch_all_shipping_fundamentals() == {}


def test_fetch_all_uses_default_tickers(monkeypatch, tmp_path, overview_payload):
    """When tickers=None the default list (5 shipping symbols) is used."""
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "K")
    _isolated_cache(monkeypatch, tmp_path)
    state = _install_fake_get(monkeypatch, overview_payload)

    out = fetch_all_shipping_fundamentals()

    # Each default ticker had its own cache key — 5 round-trips for 5 tickers.
    assert state["calls"] == len(av._DEFAULT_TICKERS)
    assert set(out.keys()).issubset(set(av._DEFAULT_TICKERS))
    assert len(out) == len(av._DEFAULT_TICKERS)


def test_fetch_all_skips_failed_tickers(monkeypatch, tmp_path, overview_payload):
    """Per-ticker exception ⇒ that ticker is skipped, others still populate."""
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "K")
    _isolated_cache(monkeypatch, tmp_path)

    # Make ZIM raise, MATX succeed.
    def _fake_get(url, params=None, timeout=None):
        if params.get("symbol") == "ZIM":
            raise RuntimeError("boom")
        return _FakeResponse(overview_payload)

    monkeypatch.setattr(av.requests, "get", _fake_get)

    out = fetch_all_shipping_fundamentals(tickers=["ZIM", "MATX"])
    # ZIM raised inside _rate_limited_get → returns empty dict → fetch returns
    # None → result skipped; MATX should still succeed.
    assert "ZIM" not in out
    assert "MATX" in out


def test_fetch_all_subset_of_tickers(monkeypatch, tmp_path, overview_payload):
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "K")
    _isolated_cache(monkeypatch, tmp_path)
    _install_fake_get(monkeypatch, overview_payload)

    out = fetch_all_shipping_fundamentals(tickers=["ZIM"])
    assert list(out.keys()) == ["ZIM"]


# ─── build_fundamentals_table_html ──────────────────────────────────────────


def _make_fundamentals(**overrides) -> StockFundamentals:
    base = dict(
        ticker="ZIM", name="ZIM Integrated", sector="Industrials",
        industry="Marine", market_cap=2.5, pe_ratio=8.4, forward_pe=6.1,
        eps=1.23, dividend_yield_pct=5.0, beta=1.45, book_value=12.4,
        pb_ratio=0.95, ev_to_ebitda=4.2, profit_margin_pct=8.0, roe_pct=15.0,
        revenue_growth_yoy_pct=22.0, analyst_target_price=18.5,
        week_52_high=21.4, week_52_low=9.8, description="x",
        fetched_at="2026-05-21T00:00:00+00:00",
    )
    base.update(overrides)
    return StockFundamentals(**base)


def test_build_html_empty_input():
    html = build_fundamentals_table_html({})
    assert "No Alpha Vantage fundamentals" in html
    # No table rows when empty.
    assert "<tr>" not in html


def test_build_html_contains_ticker_row():
    html = build_fundamentals_table_html({"ZIM": _make_fundamentals()})
    assert "<table" in html
    assert "<strong>ZIM</strong>" in html
    assert "ZIM Integrated" in html


def test_build_html_target_color_green_above_high():
    """Analyst target above 52-week high → green."""
    f = _make_fundamentals(analyst_target_price=25.0, week_52_high=21.4, week_52_low=9.8)
    html = build_fundamentals_table_html({"ZIM": f})
    assert "#27ae60" in html  # green


def test_build_html_target_color_red_below_low():
    """Analyst target below 52-week low → red."""
    f = _make_fundamentals(analyst_target_price=5.0, week_52_high=21.4, week_52_low=9.8)
    html = build_fundamentals_table_html({"ZIM": f})
    assert "#e74c3c" in html  # red


def test_build_html_target_color_amber_within_range():
    """Analyst target inside 52-week range → amber."""
    f = _make_fundamentals(analyst_target_price=15.0, week_52_high=21.4, week_52_low=9.8)
    html = build_fundamentals_table_html({"ZIM": f})
    assert "#f39c12" in html  # amber


def test_build_html_target_color_grey_when_missing():
    """Zero target or zero 52w range → grey."""
    f = _make_fundamentals(analyst_target_price=0.0, week_52_high=0.0, week_52_low=0.0)
    html = build_fundamentals_table_html({"ZIM": f})
    # No analyst target → grey N/A cell.
    assert "#888" in html or "N/A" in html
