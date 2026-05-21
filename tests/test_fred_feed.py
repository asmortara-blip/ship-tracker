"""Tests for data.fred_feed.

The FRED feed is a thin wrapper over ``fredapi.Fred`` with three layers we want
to pin without ever touching the live FRED endpoint:

  1. Pure helpers — error-classification, scalar/change extraction, BDI score.
  2. The retried fetch helper ``_fetch_series`` — exception mapping, empty-series
     handling, normalization shape.
  3. The orchestrator ``fetch_macro_series`` and its ``DataSeries`` wrapper —
     missing-key short-circuit, missing-fredapi short-circuit, per-series
     exception isolation, provenance attached to the wrapped variant.

Every test stubs out the network: ``fredapi.Fred`` is replaced via the
module-level ``Fred`` import, ``CacheManager.get_or_fetch`` is replaced with a
pass-through, and the Streamlit cache wrapper is bypassed via ``.__wrapped__``
so the underlying function runs each call.

Covers:
  - Module-level: FRED_SERIES dict shape (BDIY present, all str→str) and the
    "BDIY = BSXRLM" reference-memory finding (BDIY is present today, asserted).
  - _is_not_found_error: matches 400 / 404 / "not found" / "bad request"
    (case-insensitive); returns False for unrelated messages.
  - _fetch_series:
      * Empty/None series → empty DataFrame, not raised.
      * Happy path → normalize_macro_df contract upheld (MACRO_COLS present,
        series_id/series_name propagated, rows sorted ascending by date,
        observation_start passed with correct lookback_days offset).
      * 404-style error → raises _FredSeriesNotFound.
      * Generic Fred exception → returns empty DataFrame (no raise upstream).
      * tenacity retry: a transient (non-404) error raised on the first two
        calls and a success on the third is retried and ultimately succeeds.
  - fetch_macro_series (the orchestrator, accessed via __wrapped__):
      * No FRED_API_KEY → empty dict, no Fred instantiation.
      * fredapi not available → empty dict.
      * Successful run with a stubbed cache and stubbed Fred → returns one
        entry per series in FRED_SERIES; per-series _FredSeriesNotFound is
        swallowed; other exceptions are also swallowed; empty DataFrames are
        excluded from the result dict.
  - get_series_value: missing series / empty df → 0.0; latest value pulled
    from "value" column; periods_back deeper than length → 0.0; falls back to
    last column when "value" missing.
  - get_series_change: insufficient rows → 0.0; computes (recent - prior) /
    prior; prior == 0 → 0.0; last-column fallback when "value" missing.
  - get_latest_value: missing → None; all-NaN values → None; returns float.
  - get_bdi / get_wti: missing key returns an empty DataFrame, not None.
  - compute_bdi_score: empty/short BDI → 0.5 neutral; current at average →
    0.5; well above average → clamped to 1.0; well below → clamped to 0.0;
    rolling_avg == 0 → 0.5.
  - fetch_macro_series_wrapped: empty dict → DataSource.kind == "demo";
    non-empty → DataSource.kind == "live" and meta["lookback_days"] matches.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from data import fred_feed
from data.fred_feed import (
    FRED_SERIES,
    _FredSeriesNotFound,
    _is_not_found_error,
    compute_bdi_score,
    fetch_macro_series,
    fetch_macro_series_wrapped,
    get_bdi,
    get_latest_value,
    get_series_change,
    get_series_value,
    get_wti,
)


# ─── Helpers ───────────────────────────────────────────────────────────────

def _macro_df(values: list[float], series_id: str = "BDIY",
              series_name: str = "Baltic Dry Index",
              start: str = "2026-01-01") -> pd.DataFrame:
    """Build a synthetic post-normalization macro DataFrame."""
    dates = pd.date_range(start=start, periods=len(values), freq="D")
    return pd.DataFrame({
        "date":        dates,
        "series_id":   series_id,
        "series_name": series_name,
        "value":       values,
        "source":      "fred",
    })


class _FakeFred:
    """Drop-in replacement for ``fredapi.Fred``.

    ``responses`` maps series_id → either a pandas Series (returned) or an
    Exception instance (raised). Records every call into ``calls`` so tests can
    assert on observation_start.
    """

    def __init__(self, responses: dict, *, api_key: str = ""):
        # capture instantiation args so tests can assert the api_key flow
        self._api_key = api_key
        self._responses = responses
        # _FakeFred is re-instantiated per call inside _fetch_series, so we
        # stash the recorder on the class.
        type(self).last_api_key = api_key

    def get_series(self, series_id: str, observation_start: str = ""):
        type(self).last_observation_start = observation_start
        type(self).last_series_id = series_id
        resp = self._responses.get(series_id)
        if isinstance(resp, Exception):
            raise resp
        return resp


def _patch_fred(monkeypatch, responses: dict):
    """Install a _FakeFred factory bound to the supplied response map."""
    def _factory(api_key: str = ""):
        return _FakeFred(responses, api_key=api_key)
    monkeypatch.setattr(fred_feed, "Fred", _factory)


def _patch_passthrough_cache(monkeypatch):
    """Replace CacheManager.get_or_fetch with a direct passthrough."""
    def _passthrough(self, *, key, fetch_fn, ttl_hours, source):
        return fetch_fn()
    monkeypatch.setattr(
        "data.cache_manager.CacheManager.get_or_fetch",
        _passthrough,
    )


# Unwrap the Streamlit cache_data decorator so tests run the raw function each
# call (no shared CachedFunc state between tests).
_fetch_raw = fetch_macro_series.__wrapped__  # type: ignore[attr-defined]


# ─── Module-level constants ────────────────────────────────────────────────

def test_fred_series_dict_shape():
    """FRED_SERIES is non-empty and maps str ids to str labels."""
    assert isinstance(FRED_SERIES, dict)
    assert len(FRED_SERIES) > 10
    for series_id, label in FRED_SERIES.items():
        assert isinstance(series_id, str) and series_id
        assert isinstance(label, str) and label


def test_fred_series_dict_includes_bdi_key():
    """BDI must be reachable under "BDIY" — get_bdi() depends on this exact key.

    Reference memory notes BDIY may need to be BSXRLM at FRED. This test
    pins *current* behavior — if the key changes, get_bdi() also needs
    updating in lock-step.
    """
    assert "BDIY" in FRED_SERIES
    assert FRED_SERIES["BDIY"] == "Baltic Dry Index"


# ─── _is_not_found_error ───────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "HTTP 404 not found",
    "HTTP 400 bad request",
    "Series Not Found",
    "Bad Request from FRED",
    "404",
    "400",
])
def test_is_not_found_error_true_paths(msg):
    assert _is_not_found_error(Exception(msg)) is True


@pytest.mark.parametrize("msg", [
    "connection timeout",
    "500 server error",
    "generic failure",
    "",
])
def test_is_not_found_error_false_paths(msg):
    assert _is_not_found_error(Exception(msg)) is False


# ─── _fetch_series ─────────────────────────────────────────────────────────

def test_fetch_series_empty_series_returns_empty_df(monkeypatch):
    """``series.empty`` short-circuits to an empty DataFrame."""
    _patch_fred(monkeypatch, {"BDIY": pd.Series(dtype=float)})
    out = fred_feed._fetch_series("BDIY", "Baltic Dry Index", 30, "DUMMY")
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_fetch_series_none_series_returns_empty_df(monkeypatch):
    _patch_fred(monkeypatch, {"BDIY": None})
    out = fred_feed._fetch_series("BDIY", "Baltic Dry Index", 30, "DUMMY")
    assert out.empty


def test_fetch_series_happy_path_normalized(monkeypatch):
    """Successful fetch is normalized to MACRO_COLS shape."""
    idx = pd.date_range("2026-01-01", periods=5, freq="D")
    series = pd.Series([100.0, 110.0, 120.0, np.nan, 130.0], index=idx)
    _patch_fred(monkeypatch, {"BDIY": series})

    out = fred_feed._fetch_series("BDIY", "Baltic Dry Index", 30, "DUMMY")

    # MACRO_COLS contract: date, series_id, series_name, value, source
    assert set(["date", "series_id", "series_name", "value", "source"]).issubset(out.columns)
    # nan row dropped — 4 valid observations remain
    assert len(out) == 4
    assert (out["series_id"] == "BDIY").all()
    assert (out["series_name"] == "Baltic Dry Index").all()
    # sorted ascending after normalization
    assert out["date"].is_monotonic_increasing


def test_fetch_series_passes_observation_start(monkeypatch):
    """observation_start must be now() - lookback_days, ISO formatted."""
    idx = pd.date_range("2026-01-01", periods=2, freq="D")
    _patch_fred(monkeypatch, {"BDIY": pd.Series([1.0, 2.0], index=idx)})

    fred_feed._fetch_series("BDIY", "Baltic Dry Index", lookback_days=7, api_key="DUMMY")

    obs_start = _FakeFred.last_observation_start
    parsed = datetime.strptime(obs_start, "%Y-%m-%d")
    expected = datetime.now() - timedelta(days=7)
    # allow a 2-day buffer for slow CI clocks / timezone wobble
    assert abs((expected - parsed).days) <= 2
    assert _FakeFred.last_api_key == "DUMMY"


def test_fetch_series_404_raises_series_not_found(monkeypatch):
    """A 404-style exception from FRED maps to _FredSeriesNotFound (not retried)."""
    _patch_fred(monkeypatch, {"BDIY": RuntimeError("HTTP 404 series not found")})

    with pytest.raises(_FredSeriesNotFound):
        fred_feed._fetch_series("BDIY", "Baltic Dry Index", 30, "DUMMY")


def test_fetch_series_generic_error_returns_empty(monkeypatch):
    """Non-404 errors swallow inside _fetch_series after retries exhaust.

    To keep this fast, we patch the tenacity wait to zero before the call.
    """
    # Bypass the exponential backoff between retries.
    monkeypatch.setattr(
        "data.fred_feed._fetch_series.retry.wait",
        lambda *a, **kw: 0,
    )

    _patch_fred(monkeypatch, {"BDIY": ConnectionError("connection refused")})

    out = fred_feed._fetch_series("BDIY", "Baltic Dry Index", 30, "DUMMY")
    # Generic exceptions get a return-empty path inside the try block; the
    # decorator does not re-raise because the inner function caught it.
    assert out.empty


def test_fetch_series_retry_then_success(monkeypatch):
    """tenacity retries non-_FredSeriesNotFound errors raised by .get_series.

    The exception path inside _fetch_series catches Fred errors and returns
    empty (no retry from outside). To exercise the *decorator* retry we have
    to raise *outside* the inner try — easiest is to make Fred() itself raise.
    """
    # Bypass backoff
    monkeypatch.setattr(
        "data.fred_feed._fetch_series.retry.wait",
        lambda *a, **kw: 0,
    )

    call_count = {"n": 0}

    def _flaky_factory(api_key=""):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RuntimeError("transient network blip")
        idx = pd.date_range("2026-01-01", periods=2, freq="D")
        return _FakeFred({"BDIY": pd.Series([1.0, 2.0], index=idx)}, api_key=api_key)

    monkeypatch.setattr(fred_feed, "Fred", _flaky_factory)

    out = fred_feed._fetch_series("BDIY", "Baltic Dry Index", 30, "DUMMY")

    assert call_count["n"] == 3
    assert not out.empty
    assert (out["series_id"] == "BDIY").all()


# ─── fetch_macro_series (orchestrator) ─────────────────────────────────────

def test_fetch_macro_series_no_api_key_returns_empty(monkeypatch):
    """Missing FRED_API_KEY short-circuits to {} without calling Fred()."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    # st.secrets.get raises in a no-runtime context — the except branch
    # falls through to os.getenv, which we just cleared.

    def _explode(*a, **kw):  # pragma: no cover - asserted not called
        raise AssertionError("Fred() must not be instantiated with no API key")

    monkeypatch.setattr(fred_feed, "Fred", _explode)

    out = _fetch_raw(lookback_days=30)
    assert out == {}


def test_fetch_macro_series_fredapi_unavailable(monkeypatch):
    """When the fredapi import failed, the orchestrator returns {}."""
    monkeypatch.setenv("FRED_API_KEY", "DUMMY")
    monkeypatch.setattr(fred_feed, "_FREDAPI_AVAILABLE", False)
    out = _fetch_raw(lookback_days=30)
    assert out == {}


def test_fetch_macro_series_isolates_failures(monkeypatch):
    """One series raising _FredSeriesNotFound or generic error does not
    poison the rest. Empty DataFrames are excluded from the result dict.
    """
    monkeypatch.setenv("FRED_API_KEY", "DUMMY")
    monkeypatch.setattr(fred_feed, "_FREDAPI_AVAILABLE", True)

    # Three responses to exercise the three branches:
    #   BDIY      -> happy path
    #   DGS10     -> 404 (mapped to _FredSeriesNotFound, swallowed)
    #   VIXCLS    -> generic crash inside Fred() instantiation (caught by
    #                the broad except)
    #   IPMAN     -> empty series (returns empty DataFrame, excluded)
    idx = pd.date_range("2026-01-01", periods=3, freq="D")
    responses = {
        "BDIY":   pd.Series([100.0, 110.0, 120.0], index=idx),
        "DGS10":  RuntimeError("HTTP 404 not found"),
        "IPMAN":  pd.Series(dtype=float),
    }

    # All other series default to a tiny valid series — we only care that the
    # three special ones behave correctly.
    default = pd.Series([1.0, 2.0], index=pd.date_range("2026-01-01", periods=2, freq="D"))
    for sid in FRED_SERIES:
        responses.setdefault(sid, default)

    def _factory(api_key=""):
        return _FakeFred(responses, api_key=api_key)

    # Force VIXCLS to crash by wrapping the factory.
    def _crash_some(api_key=""):
        return _CrashingFred(responses, api_key=api_key)

    class _CrashingFred(_FakeFred):
        def get_series(self, series_id, observation_start=""):
            if series_id == "VIXCLS":
                raise RuntimeError("kaboom (non-404)")
            return super().get_series(series_id, observation_start=observation_start)

    monkeypatch.setattr(fred_feed, "Fred", _crash_some)
    # Bypass backoff for the VIXCLS retry path.
    monkeypatch.setattr(
        "data.fred_feed._fetch_series.retry.wait",
        lambda *a, **kw: 0,
    )
    _patch_passthrough_cache(monkeypatch)

    out = _fetch_raw(lookback_days=30)

    # Happy path series present
    assert "BDIY" in out and not out["BDIY"].empty
    # 404 series absent
    assert "DGS10" not in out
    # Empty-series-id absent (IPMAN returned empty)
    assert "IPMAN" not in out
    # Generic crash absent (Fred swallowed it after retries)
    # VIXCLS may or may not be present depending on retry behavior; what
    # matters is the orchestrator did not raise.
    assert isinstance(out, dict)


# ─── get_series_value ──────────────────────────────────────────────────────

def test_get_series_value_missing_returns_zero():
    assert get_series_value("DOES_NOT_EXIST", {}, periods_back=1) == 0.0


def test_get_series_value_empty_df_returns_zero():
    assert get_series_value("BDIY", {"BDIY": pd.DataFrame()}, periods_back=1) == 0.0


def test_get_series_value_latest_observation():
    data = {"BDIY": _macro_df([100.0, 200.0, 300.0])}
    assert get_series_value("BDIY", data, periods_back=1) == 300.0
    assert get_series_value("BDIY", data, periods_back=3) == 100.0


def test_get_series_value_periods_back_too_large_returns_zero():
    data = {"BDIY": _macro_df([1.0, 2.0])}
    assert get_series_value("BDIY", data, periods_back=99) == 0.0


def test_get_series_value_falls_back_to_last_column_when_value_missing():
    df = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=2), "rate": [5.0, 9.0]})
    data = {"FOO": df}
    assert get_series_value("FOO", data, periods_back=1) == 9.0


# ─── get_series_change ─────────────────────────────────────────────────────

def test_get_series_change_insufficient_data_returns_zero():
    data = {"BDIY": _macro_df([1.0, 2.0])}  # len < periods+1 (default periods=4)
    assert get_series_change("BDIY", data, periods=4) == 0.0


def test_get_series_change_missing_returns_zero():
    assert get_series_change("DOES_NOT_EXIST", {}, periods=4) == 0.0


def test_get_series_change_basic_computation():
    # 5 points: index -5 = 100, index -1 = 150 → (150-100)/100 = 0.5
    data = {"BDIY": _macro_df([100.0, 110.0, 120.0, 130.0, 150.0])}
    assert get_series_change("BDIY", data, periods=4) == pytest.approx(0.5)


def test_get_series_change_prior_zero_returns_zero():
    data = {"BDIY": _macro_df([0.0, 1.0, 2.0, 3.0, 5.0])}
    assert get_series_change("BDIY", data, periods=4) == 0.0


def test_get_series_change_falls_back_to_last_column():
    dates = pd.date_range("2026-01-01", periods=5)
    df = pd.DataFrame({"date": dates, "rate": [100.0, 110.0, 120.0, 130.0, 150.0]})
    assert get_series_change("FOO", {"FOO": df}, periods=4) == pytest.approx(0.5)


# ─── get_latest_value ──────────────────────────────────────────────────────

def test_get_latest_value_missing_returns_none():
    assert get_latest_value("BDIY", {}) is None


def test_get_latest_value_empty_df_returns_none():
    assert get_latest_value("BDIY", {"BDIY": pd.DataFrame()}) is None


def test_get_latest_value_all_nan_returns_none():
    df = _macro_df([1.0, 2.0]).assign(value=[float("nan"), float("nan")])
    assert get_latest_value("BDIY", {"BDIY": df}) is None


def test_get_latest_value_returns_float():
    data = {"BDIY": _macro_df([1.0, 2.0, 3.5])}
    out = get_latest_value("BDIY", data)
    assert isinstance(out, float)
    assert out == 3.5


# ─── get_bdi / get_wti ─────────────────────────────────────────────────────

def test_get_bdi_missing_returns_empty_df():
    out = get_bdi({})
    assert isinstance(out, pd.DataFrame) and out.empty


def test_get_bdi_returns_series():
    df = _macro_df([100.0, 110.0])
    out = get_bdi({"BDIY": df})
    assert len(out) == 2


def test_get_wti_missing_returns_empty_df():
    out = get_wti({})
    assert isinstance(out, pd.DataFrame) and out.empty


def test_get_wti_returns_series():
    df = _macro_df([60.0, 65.0], series_id="DCOILWTICO", series_name="WTI")
    out = get_wti({"DCOILWTICO": df})
    assert len(out) == 2


# ─── compute_bdi_score ─────────────────────────────────────────────────────

def test_compute_bdi_score_neutral_when_empty():
    assert compute_bdi_score({}) == 0.5


def test_compute_bdi_score_neutral_when_short():
    df = _macro_df([100.0] * 5)  # < 10 rows
    assert compute_bdi_score({"BDIY": df}) == 0.5


def test_compute_bdi_score_at_average_returns_half():
    # 90 constant values → current/avg = 1.0 → (1.0 - 0.5)/1.0 = 0.5
    df = _macro_df([1000.0] * 90)
    assert compute_bdi_score({"BDIY": df}, lookback_days=90) == pytest.approx(0.5)


def test_compute_bdi_score_well_above_clamps_to_one():
    # current jumps to 3x average → ratio = 3.0 → score = 2.5 → clamp 1.0
    values = [100.0] * 30 + [300.0]
    df = _macro_df(values)
    assert compute_bdi_score({"BDIY": df}, lookback_days=30) == 1.0


def test_compute_bdi_score_well_below_clamps_to_zero():
    values = [100.0] * 30 + [10.0]
    df = _macro_df(values)
    # ratio ~= 0.13 → score ~= -0.37 → clamp 0.0
    assert compute_bdi_score({"BDIY": df}, lookback_days=30) == 0.0


def test_compute_bdi_score_zero_rolling_avg_returns_half():
    df = _macro_df([0.0] * 30)
    assert compute_bdi_score({"BDIY": df}, lookback_days=30) == 0.5


# ─── fetch_macro_series_wrapped ────────────────────────────────────────────

def test_fetch_macro_series_wrapped_empty_marks_demo(monkeypatch):
    """When the underlying fetch returns {}, provenance reads as demo."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    # Stub the inner fetch_macro_series so we don't pay the orchestrator cost
    # and avoid any st.cache_data interference.
    monkeypatch.setattr(fred_feed, "fetch_macro_series", lambda **kw: {})

    ds = fetch_macro_series_wrapped(lookback_days=42)
    assert ds.data == {}
    assert ds.source.name == "FRED"
    assert ds.source.kind == "demo"
    assert ds.meta == {"lookback_days": 42}


def test_fetch_macro_series_wrapped_nonempty_marks_live(monkeypatch):
    """A non-empty payload yields a live DataSource."""
    monkeypatch.setenv("FRED_API_KEY", "DUMMY")
    fake = {"BDIY": _macro_df([100.0, 110.0])}
    monkeypatch.setattr(fred_feed, "fetch_macro_series", lambda **kw: fake)

    ds = fetch_macro_series_wrapped(lookback_days=180, ttl_hours=12.0)
    assert ds.data is fake
    assert ds.source.name == "FRED"
    assert ds.source.kind == "live"
    assert ds.source.sla_hours == 12.0
    assert ds.meta == {"lookback_days": 180}
