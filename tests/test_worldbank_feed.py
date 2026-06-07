"""Tests for ``data/worldbank_feed.py``.

The World Bank feed wraps a public REST endpoint behind a ``CacheManager``
and a thin layer of helpers that downstream code (port_demand_forecaster,
demand_analyzer) relies on for TEU throughput and Liner Shipping
Connectivity Index lookups. The behavioural contract this file pins:

* ``_iso2_to_iso3`` round-trips the documented alpha-2 ↔ alpha-3 table,
  upper-cases its input, and passes unknown codes through unchanged.
* ``_fetch_indicator`` builds the correct URL / query params from a list
  of ISO2 country codes and the indicator id.
* The parser drops records whose ``value`` is ``None``, coerces ``date``
  to ``int(year)``, ``value`` to ``float``, and stamps ``source="worldbank"``.
* Malformed JSON shapes the API can return (``[meta_only]``, ``[meta, []]``,
  ``[]``, ``None``) all map to an *empty* DataFrame — never an exception.
* Network failures (``RequestException`` from ``requests.get``) and
  HTTP errors (``raise_for_status``) are swallowed by the inner
  try/except and yield an empty DataFrame. They do **not** propagate to
  the tenacity retry decorator — a finding worth pinning.
* ``fetch_port_throughput`` calls ``_fetch_indicator`` once per indicator
  via ``CacheManager.get_or_fetch``, drops indicators whose response is
  empty, and returns the survivors keyed by indicator id.
* ``get_teu_for_country`` returns 0.0 when the TEU frame is missing /
  empty / contains no rows for the requested country, otherwise returns
  the latest year's value in **millions** with the per-port traffic
  weight applied when ``port_locode`` is supplied for a multi-port
  country.
* ``get_connectivity_for_country`` mirrors the same missing-data
  contract but returns the raw LSCI value (no unit conversion, no
  weighting).

Every test mocks ``requests.get`` via ``monkeypatch`` — no live calls.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest
import requests

import data.worldbank_feed as wb
from data.worldbank_feed import (
    WB_INDICATORS,
    _ISO3_TO_ISO2,
    _fetch_indicator,
    _iso2_to_iso3,
    fetch_port_throughput,
    get_connectivity_for_country,
    get_teu_for_country,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _make_response(payload: Any, status: int = 200) -> MagicMock:
    """Build a ``requests.Response``-shaped MagicMock returning ``payload``."""
    resp = MagicMock()
    resp.json.return_value = payload
    resp.status_code = status
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def _wb_record(
    *,
    year: int,
    value: float | None,
    iso3: str = "USA",
    iso2: str = "US",
) -> dict:
    """Shape of a single record in the second element of the WB response."""
    return {
        "indicator": {"id": "IS.SHP.GOOD.TU", "value": "Container Port Traffic"},
        "country": {"id": iso2, "value": "United States"},
        "countryiso3code": iso3,
        "date": str(year),
        "value": value,
        "unit": "",
        "obs_status": "",
        "decimal": 0,
    }


def _wb_payload(records: list[dict]) -> list[Any]:
    """A well-formed two-element World Bank response (meta + records)."""
    return [
        {"page": 1, "pages": 1, "per_page": 500, "total": len(records)},
        records,
    ]


# ── _iso2_to_iso3: country code normalization ──────────────────────────────


def test_iso2_to_iso3_round_trips_documented_table():
    """Every alpha-3 in the module's table must round-trip via the inverse."""
    for iso3, iso2 in _ISO3_TO_ISO2.items():
        assert _iso2_to_iso3(iso2) == iso3


def test_iso2_to_iso3_uppercases_input():
    """Lower-case ISO2 codes from the API should still resolve correctly."""
    assert _iso2_to_iso3("us") == "USA"
    assert _iso2_to_iso3("cn") == "CHN"
    assert _iso2_to_iso3("nl") == "NLD"


def test_iso2_to_iso3_passes_unknown_codes_through_unchanged():
    """Unknown codes (e.g. WB regional aggregates) must not raise."""
    assert _iso2_to_iso3("ZZ") == "ZZ"
    assert _iso2_to_iso3("") == ""


# ── _fetch_indicator: URL/params construction and response parsing ─────────


def test_fetch_indicator_builds_correct_url_and_params(monkeypatch):
    """The URL and query params must follow the documented WB v2 schema."""
    captured: dict[str, Any] = {}

    def fake_get(url: str, params: dict, timeout: int) -> MagicMock:
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return _make_response(_wb_payload([]))

    monkeypatch.setattr(wb.requests, "get", fake_get)
    _fetch_indicator("IS.SHP.GOOD.TU", "TEU", ["US", "CN", "NL"], 7)

    assert captured["url"] == "https://api.worldbank.org/v2/country/US;CN;NL/indicator/IS.SHP.GOOD.TU"
    assert captured["params"] == {"format": "json", "per_page": 500, "mrv": 7}
    assert captured["timeout"] == 30


def test_fetch_indicator_parses_well_formed_response(monkeypatch):
    """A two-row response should yield a two-row DataFrame with the
    schema columns and correct dtypes."""
    payload = _wb_payload([
        _wb_record(year=2023, value=47_303_000.0, iso3="USA", iso2="US"),
        _wb_record(year=2022, value=46_010_000.0, iso3="USA", iso2="US"),
    ])
    monkeypatch.setattr(wb.requests, "get", lambda *a, **k: _make_response(payload))

    df = _fetch_indicator("IS.SHP.GOOD.TU", "Container Port Traffic", ["US"], 7)

    expected_cols = {
        "year", "country_iso3", "country_iso2",
        "indicator_id", "indicator_name", "value", "source",
    }
    assert set(df.columns) == expected_cols
    assert len(df) == 2
    assert df["year"].dtype.kind == "i"
    assert df["value"].dtype.kind == "f"
    assert (df["source"] == "worldbank").all()
    assert (df["country_iso3"] == "USA").all()
    assert (df["indicator_id"] == "IS.SHP.GOOD.TU").all()


def test_fetch_indicator_drops_null_values(monkeypatch):
    """Records with ``value=None`` (common in WB time-series) are filtered out."""
    payload = _wb_payload([
        _wb_record(year=2023, value=1_000_000.0),
        _wb_record(year=2024, value=None),     # missing — drop
        _wb_record(year=2022, value=999_000.0),
        _wb_record(year=2021, value=None),     # missing — drop
    ])
    monkeypatch.setattr(wb.requests, "get", lambda *a, **k: _make_response(payload))

    df = _fetch_indicator("IS.SHP.GOOD.TU", "TEU", ["US"], 7)

    assert len(df) == 2
    assert set(df["year"]) == {2022, 2023}


def test_fetch_indicator_resolves_iso3_from_payload(monkeypatch):
    """The ``countryiso3code`` field on each record is preserved (already alpha-3)."""
    payload = _wb_payload([
        _wb_record(year=2023, value=27_000_000.0, iso3="CHN", iso2="CN"),
        _wb_record(year=2023, value=10_000_000.0, iso3="NLD", iso2="NL"),
    ])
    monkeypatch.setattr(wb.requests, "get", lambda *a, **k: _make_response(payload))

    df = _fetch_indicator("IS.SHP.GOOD.TU", "TEU", ["CN", "NL"], 7)
    assert set(df["country_iso3"]) == {"CHN", "NLD"}
    assert set(df["country_iso2"]) == {"CN", "NL"}


# ── _fetch_indicator: empty / malformed responses ──────────────────────────


def test_fetch_indicator_returns_empty_on_none_response(monkeypatch):
    """``data is None`` (API totally absent) yields an empty DataFrame."""
    monkeypatch.setattr(wb.requests, "get", lambda *a, **k: _make_response(None))
    df = _fetch_indicator("IS.SHP.GOOD.TU", "TEU", ["US"], 7)
    assert df.empty


def test_fetch_indicator_returns_empty_on_meta_only_response(monkeypatch):
    """``len(data) < 2`` (only metadata, no records list) yields empty."""
    monkeypatch.setattr(wb.requests, "get", lambda *a, **k: _make_response([{"message": "no data"}]))
    df = _fetch_indicator("IS.SHP.GOOD.TU", "TEU", ["US"], 7)
    assert df.empty


def test_fetch_indicator_returns_empty_on_empty_records_list(monkeypatch):
    """``data[1] == []`` yields empty (the ``not data[1]`` branch)."""
    monkeypatch.setattr(wb.requests, "get", lambda *a, **k: _make_response(_wb_payload([])))
    df = _fetch_indicator("IS.SHP.GOOD.TU", "TEU", ["US"], 7)
    assert df.empty


def test_fetch_indicator_returns_empty_on_all_null_values(monkeypatch):
    """Records all ``value=None`` ⇒ ``rows == []`` ⇒ empty (not crash)."""
    payload = _wb_payload([
        _wb_record(year=2023, value=None),
        _wb_record(year=2022, value=None),
    ])
    monkeypatch.setattr(wb.requests, "get", lambda *a, **k: _make_response(payload))
    df = _fetch_indicator("IS.SHP.GOOD.TU", "TEU", ["US"], 7)
    assert df.empty


def test_fetch_indicator_retries_http_error_then_empty(monkeypatch):
    """A 5xx status (``raise_for_status`` raises ``HTTPError`` — a subclass
    of ``RequestException``) is now retried by tenacity, then degrades to
    empty after attempts exhaust."""
    # Bypass exponential backoff between retries.
    monkeypatch.setattr(
        "data.worldbank_feed._wb_http_get.retry.wait",
        lambda *a, **kw: 0,
    )
    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        return _make_response({}, status=503)

    monkeypatch.setattr(wb.requests, "get", fake_get)
    df = _fetch_indicator("IS.SHP.GOOD.TU", "TEU", ["US"], 7)
    # 3 attempts before degrading to empty.
    assert calls["n"] == 3
    assert df.empty


def test_fetch_indicator_retries_network_error_then_empty(monkeypatch):
    """A ``ConnectionError`` (``RequestException``) propagates to tenacity,
    is retried up to ``stop_after_attempt(3)`` times, then yields empty."""
    monkeypatch.setattr(
        "data.worldbank_feed._wb_http_get.retry.wait",
        lambda *a, **kw: 0,
    )
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise requests.ConnectionError("DNS failure")

    monkeypatch.setattr(wb.requests, "get", boom)
    df = _fetch_indicator("IS.SHP.GOOD.TU", "TEU", ["US"], 7)
    assert calls["n"] == 3
    assert df.empty


def test_fetch_indicator_swallows_invalid_json(monkeypatch):
    """A response whose ``.json()`` raises a non-network exception (e.g.
    ``ValueError``) is NOT retried — it's a 200 with a broken body. The
    function still returns an empty DataFrame."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.side_effect = ValueError("not JSON")
    monkeypatch.setattr(wb.requests, "get", lambda *a, **k: resp)

    df = _fetch_indicator("IS.SHP.GOOD.TU", "TEU", ["US"], 7)
    assert df.empty


# ── fetch_port_throughput: cache integration + indicator iteration ─────────


class _StubCache:
    """A drop-in CacheManager that runs ``fetch_fn`` directly and counts calls.

    Avoids touching the real parquet cache layer and the ``@st.cache_data``
    wrapper around ``fetch_port_throughput`` (Streamlit silently no-ops
    outside a runtime, so calls pass through to the function body).
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_or_fetch(self, *, key, fetch_fn, ttl_hours, source):
        self.calls.append({"key": key, "ttl_hours": ttl_hours, "source": source})
        return fetch_fn()


def test_fetch_port_throughput_calls_indicator_per_id(monkeypatch):
    """One ``_fetch_indicator`` call per ``WB_INDICATORS`` entry; results
    keyed by indicator id; cache source is ``"worldbank"``."""
    cache = _StubCache()

    seen_indicators: list[str] = []

    def fake_fetch(indicator_id, indicator_name, iso2_codes, years_back):
        seen_indicators.append(indicator_id)
        # Return a 1-row frame so it survives the empty-filter.
        return pd.DataFrame([{
            "year": 2023, "country_iso3": "USA", "country_iso2": "US",
            "indicator_id": indicator_id, "indicator_name": indicator_name,
            "value": 1.0, "source": "worldbank",
        }])

    monkeypatch.setattr(wb, "_fetch_indicator", fake_fetch)
    # Bypass @st.cache_data — its hasher can't handle our stub cache.
    out = fetch_port_throughput.__wrapped__(cache=cache, ttl_hours=24.0, years_back=5)

    assert set(seen_indicators) == set(WB_INDICATORS.keys())
    assert set(out.keys()) == set(WB_INDICATORS.keys())
    assert all(call["source"] == "worldbank" for call in cache.calls)
    assert all(call["ttl_hours"] == 24.0 for call in cache.calls)


def test_fetch_port_throughput_drops_empty_results(monkeypatch):
    """Indicators whose fetch returns an empty DataFrame must be omitted."""
    cache = _StubCache()

    def fake_fetch(indicator_id, *_a, **_k):
        if indicator_id == "IS.SHP.GOOD.TU":
            return pd.DataFrame([{
                "year": 2023, "country_iso3": "USA", "country_iso2": "US",
                "indicator_id": indicator_id, "indicator_name": "x",
                "value": 1.0, "source": "worldbank",
            }])
        return pd.DataFrame()

    monkeypatch.setattr(wb, "_fetch_indicator", fake_fetch)
    out = fetch_port_throughput.__wrapped__(cache=cache)

    assert list(out.keys()) == ["IS.SHP.GOOD.TU"]


def test_fetch_port_throughput_cache_key_includes_iso_codes_and_horizon(monkeypatch):
    """The cache key must encode the indicator + sorted country codes +
    years-back horizon so different horizons / universes don't collide."""
    cache = _StubCache()
    monkeypatch.setattr(wb, "_fetch_indicator", lambda *a, **k: pd.DataFrame())
    fetch_port_throughput.__wrapped__(cache=cache, years_back=7)

    for call in cache.calls:
        # Indicator id leads, "7y" trails.
        assert call["key"].endswith("_7y")
        # Sorted ISO2 codes appear in the middle (lexical ordering pins this).
        # All keys for the same run share the same sorted ISO2 segment.
    iso_segments = {call["key"].split("_", 1)[1].rsplit("_", 1)[0] for call in cache.calls}
    assert len(iso_segments) == 1, "ISO segment must be stable across indicators in one run"


def test_fetch_port_throughput_passes_only_mapped_iso3_to_iso2(monkeypatch):
    """Countries in ``PORTS`` without a row in ``_ISO3_TO_ISO2`` must be
    skipped silently (no KeyError, no spurious ISO2 codes)."""
    cache = _StubCache()
    captured_codes: list[list[str]] = []

    def fake_fetch(indicator_id, indicator_name, iso2_codes, years_back):
        captured_codes.append(list(iso2_codes))
        return pd.DataFrame()

    monkeypatch.setattr(wb, "_fetch_indicator", fake_fetch)
    fetch_port_throughput.__wrapped__(cache=cache)

    assert captured_codes, "at least one indicator should have been requested"
    iso2 = set(captured_codes[0])
    # Every emitted ISO2 code must be a value in the documented map.
    assert iso2.issubset(set(_ISO3_TO_ISO2.values()))


# ── get_teu_for_country: lookup + millions conversion + port weight ────────


def _teu_frame(rows: list[tuple[str, int, float]]) -> pd.DataFrame:
    """Helper: build a TEU frame from (iso3, year, value)."""
    return pd.DataFrame([
        {"country_iso3": iso3, "year": y, "value": v,
         "indicator_id": "IS.SHP.GOOD.TU", "country_iso2": "",
         "indicator_name": "TEU", "source": "worldbank"}
        for iso3, y, v in rows
    ])


def test_get_teu_returns_zero_when_indicator_missing():
    assert get_teu_for_country("USA", {}) == 0.0


def test_get_teu_returns_zero_when_indicator_empty():
    wb_data = {"IS.SHP.GOOD.TU": pd.DataFrame()}
    assert get_teu_for_country("USA", wb_data) == 0.0


def test_get_teu_returns_zero_when_country_missing():
    wb_data = {"IS.SHP.GOOD.TU": _teu_frame([("CHN", 2023, 27_000_000.0)])}
    assert get_teu_for_country("USA", wb_data) == 0.0


def test_get_teu_returns_latest_year_in_millions():
    """Single-port country, no weight applied: raw_teu / 1e6."""
    wb_data = {"IS.SHP.GOOD.TU": _teu_frame([
        ("SGP", 2021, 36_000_000.0),
        ("SGP", 2023, 37_500_000.0),   # latest
        ("SGP", 2022, 36_500_000.0),
    ])}
    val = get_teu_for_country("SGP", wb_data)
    assert val == pytest.approx(37.5, abs=1e-9)


def test_get_teu_applies_port_weight_for_multi_port_country():
    """USA: USLAX's *renormalized* share scales the 50 MTEU national total.

    Reads the live weight from ``PORT_TRAFFIC_WEIGHTS`` (the renormalized table,
    where USA sums to 1.0) rather than a stale magic number — the raw 0.22
    authored share renormalizes to ~0.319 so the tracked ports fully distribute
    the national total instead of dropping ~31% of it.
    """
    from ports.port_registry import PORT_TRAFFIC_WEIGHTS

    wb_data = {"IS.SHP.GOOD.TU": _teu_frame([("USA", 2023, 50_000_000.0)])}
    val = get_teu_for_country("USA", wb_data, port_locode="USLAX")
    expected = 50.0 * PORT_TRAFFIC_WEIGHTS["USA"]["USLAX"]
    assert val == pytest.approx(expected, abs=1e-9)


def test_get_teu_ignores_locode_when_no_weight_table_entry():
    """An unknown locode resolves to weight 1.0 (defensive fallback)."""
    wb_data = {"IS.SHP.GOOD.TU": _teu_frame([("USA", 2023, 50_000_000.0)])}
    val = get_teu_for_country("USA", wb_data, port_locode="XXUNK")
    assert val == pytest.approx(50.0, abs=1e-9)


def test_get_teu_skips_weight_when_locode_empty():
    """Default ``port_locode=""`` ⇒ no weighting applied."""
    wb_data = {"IS.SHP.GOOD.TU": _teu_frame([("CHN", 2023, 100_000_000.0)])}
    val = get_teu_for_country("CHN", wb_data)
    # Even though CHN is in PORT_TRAFFIC_WEIGHTS, no locode ⇒ weight = 1.0.
    assert val == pytest.approx(100.0, abs=1e-9)


# ── get_connectivity_for_country: same missing-data contract, no scaling ───


def _lsci_frame(rows: list[tuple[str, int, float]]) -> pd.DataFrame:
    return pd.DataFrame([
        {"country_iso3": iso3, "year": y, "value": v,
         "indicator_id": "IS.SHP.GCNW.XQ", "country_iso2": "",
         "indicator_name": "LSCI", "source": "worldbank"}
        for iso3, y, v in rows
    ])


def test_get_connectivity_returns_zero_when_indicator_missing():
    assert get_connectivity_for_country("USA", {}) == 0.0


def test_get_connectivity_returns_zero_when_indicator_empty():
    wb_data = {"IS.SHP.GCNW.XQ": pd.DataFrame()}
    assert get_connectivity_for_country("USA", wb_data) == 0.0


def test_get_connectivity_returns_zero_when_country_missing():
    wb_data = {"IS.SHP.GCNW.XQ": _lsci_frame([("CHN", 2023, 165.0)])}
    assert get_connectivity_for_country("USA", wb_data) == 0.0


def test_get_connectivity_returns_latest_raw_value():
    """LSCI is unitless ⇒ no /1e6 conversion, no port weighting."""
    wb_data = {"IS.SHP.GCNW.XQ": _lsci_frame([
        ("CHN", 2021, 150.0),
        ("CHN", 2023, 165.4),   # latest
        ("CHN", 2022, 158.7),
    ])}
    val = get_connectivity_for_country("CHN", wb_data)
    assert val == pytest.approx(165.4, abs=1e-9)


# ── WB_INDICATORS schema sanity ────────────────────────────────────────────


def test_wb_indicators_well_formed():
    """The indicator map must contain only WB v2-shaped IDs and human names."""
    for iid, name in WB_INDICATORS.items():
        # WB indicator IDs look like 'IS.SHP.GOOD.TU' — uppercase, dotted.
        assert iid.upper() == iid
        assert "." in iid
        assert isinstance(name, str) and name.strip()
    # The two helpers in the module read these specific IDs.
    assert "IS.SHP.GOOD.TU" in WB_INDICATORS
    assert "IS.SHP.GCNW.XQ" in WB_INDICATORS
