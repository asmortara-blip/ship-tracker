"""Tests for ``data/imf_feed.py``.

The IMF feed wraps three public CompactData SDMX-JSON endpoints (WEO GDP
forecasts, DOTS bilateral trade flows, PCPS commodity prices) behind a
``CacheManager`` and turns the deeply nested SDMX shape into flat dicts
that the shipping demand outlook consumes. The defining behavioural
contract this file pins:

* Module-level configuration
    - ``_IMF_BASE`` is the documented dataservices CompactData URL.
    - ``_WEO_COUNTRIES`` lists the 11 economies whose growth feeds the
      GDP signal (USA, CHN, DEU, JPN, KOR, SGP, NLD, GBR, FRA, IND, BRA).
    - The three pre-built endpoint URLs (``_GDP_URL``, ``_DOTS_URL``,
      ``_COMMODITY_URL``) embed the country / commodity slots correctly.
    - ``_COMMODITY_LABELS`` maps every PCPS code to a human label.

* ``_fetch_url`` (``requests.get`` monkeypatched)
    - Happy path: returns the parsed JSON dict.
    - HTTPError, Timeout, RequestException and any other ``Exception``
      all swallow and yield ``{}`` — never propagate.

* ``_cached_json`` (CacheManager isolated under tmp_path)
    - On cache miss, calls the fetch function once and round-trips the
      JSON payload through a single-cell parquet.
    - On cache hit, the fetch function is **not** called a second time.
    - When the fetch returns an empty dict the cache stays empty and
      subsequent calls return ``{}``.
    - Corrupt JSON in the cached cell falls back to ``{}``.

* ``_parse_imf_compact``
    - Returns ``{}`` for missing CompactData / DataSet keys.
    - Accepts a single dict in ``Series`` (IMF behaviour when only one
      series is returned) and treats it as a one-element list.
    - Builds the series key from every ``@``-prefixed attribute except
      ``@xmlns``.
    - Coerces ``@OBS_VALUE`` to float; rows with non-numeric values are
      silently dropped.
    - Skips observations missing either ``@TIME_PERIOD`` or
      ``@OBS_VALUE``.
    - Sorts observations by time string ascending so ``series[-1]`` is
      the latest.
    - Returns ``{}`` on any exception (broad catch).

* ``_extract_attr``
    - Returns the requested attribute value or ``None`` when absent.
    - Splits on ``|`` and only matches exact prefix ``ATTR=``.

* ``fetch_imf_data`` (public API, monkeypatched URL fetchers, CacheManager
  under tmp_path)
    - When all three endpoints return empty, the function returns ``{}``.
    - When at least one endpoint has data, the returned dict has the
      promised keys (``gdp_forecasts``, ``trade_flows``,
      ``commodity_prices``, ``fetched_at``) and ``fetched_at`` is a valid
      ISO-8601 UTC timestamp.
    - GDP forecasts: country code is taken from ``REF_AREA``, latest
      value pinned to ``series[-1][1]``.
    - DOTS trade flows: TXG indicators populate ``exports_usd``, TMG
      populate ``imports_usd``, ``latest_date`` is filled by the first
      side seen.
    - PCPS commodity prices: code resolves via ``_COMMODITY_LABELS``
      (e.g. ``PIRON`` → ``Iron Ore``); 3-month change is rounded to 2 dp
      and zero-division-safe; series shorter than 4 points keeps
      ``change_3m_pct = 0.0``.

* ``get_shipping_demand_outlook``
    - Empty input → static defaults dict (Stable / Moderate / 0.5).
    - Top-3 economies are sorted by growth descending.
    - avg growth > 3.5 → ``Accelerating``; < 2.0 → ``Decelerating``;
      otherwise ``Stable``.
    - ``_trend`` thresholds: > +2% → Rising, < -2% → Falling, else
      Stable.
    - Average of {iron, coal, soy, wheat} 3m change > 3 → High demand;
      < -3 → Low; else Moderate.
    - Composite demand score blends gdp/6, commodity map, iron trend map
      and rounds to 3 dp.

No network calls anywhere — ``requests.get`` is monkeypatched.
"""
from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest
import requests

import data.imf_feed as imf
from data.cache_manager import CacheManager


# ─── Helpers ───────────────────────────────────────────────────────────────


def _make_response(payload: Any, status: int = 200) -> MagicMock:
    """Build a ``requests.Response``-shaped MagicMock returning ``payload``."""
    resp = MagicMock()
    resp.json.return_value = payload
    resp.status_code = status
    if status >= 400:
        err = requests.HTTPError(f"HTTP {status}")
        err.response = resp
        resp.raise_for_status.side_effect = err
    else:
        resp.raise_for_status.return_value = None
    return resp


def _gdp_payload(rows: list[tuple[str, str, float]]) -> dict:
    """Build a CompactData payload for WEO GDP series.

    ``rows`` is a list of (REF_AREA, year, value) tuples; rows with the
    same REF_AREA are grouped into a single Series.
    """
    by_country: dict[str, list[tuple[str, float]]] = {}
    for country, year, value in rows:
        by_country.setdefault(country, []).append((year, value))

    series_list = []
    for country, obs in by_country.items():
        series_list.append({
            "@FREQ": "A",
            "@REF_AREA": country,
            "@INDICATOR": "NGDP_RPCH",
            "Obs": [
                {"@TIME_PERIOD": yr, "@OBS_VALUE": str(val)}
                for yr, val in obs
            ],
        })

    return {
        "CompactData": {
            "DataSet": {"Series": series_list},
        },
    }


def _dots_payload(rows: list[tuple[str, str, str, float]]) -> dict:
    """Build CompactData payload for DOTS trade flows.

    ``rows``: list of (REF_AREA, INDICATOR, period, value).
    """
    groups: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for country, ind, period, value in rows:
        groups.setdefault((country, ind), []).append((period, value))

    series_list = []
    for (country, ind), obs in groups.items():
        series_list.append({
            "@FREQ": "Q",
            "@REF_AREA": country,
            "@INDICATOR": ind,
            "@COUNTERPART_AREA": "W00",
            "Obs": [
                {"@TIME_PERIOD": p, "@OBS_VALUE": str(v)} for p, v in obs
            ],
        })

    return {
        "CompactData": {
            "DataSet": {"Series": series_list},
        },
    }


def _pcps_payload(rows: list[tuple[str, str, float]]) -> dict:
    """Build CompactData payload for PCPS commodities.

    ``rows``: list of (COMMODITY, period, value).
    """
    groups: dict[str, list[tuple[str, float]]] = {}
    for code, period, value in rows:
        groups.setdefault(code, []).append((period, value))

    series_list = []
    for code, obs in groups.items():
        series_list.append({
            "@FREQ": "M",
            "@REF_AREA": "W00",
            "@COMMODITY": code,
            "Obs": [
                {"@TIME_PERIOD": p, "@OBS_VALUE": str(v)} for p, v in obs
            ],
        })

    return {
        "CompactData": {
            "DataSet": {"Series": series_list},
        },
    }


def _patch_fetch_url(monkeypatch, payload_map: dict[str, dict]):
    """Patch ``imf._fetch_url`` to return canned payloads per label."""
    state = {"calls": []}

    def _fake(url: str, label: str) -> dict:
        state["calls"].append((url, label))
        return payload_map.get(label, {})

    monkeypatch.setattr(imf, "_fetch_url", _fake, raising=True)
    return state


# ─── Module-level configuration ────────────────────────────────────────────


def test_imf_base_url_is_compactdata_endpoint():
    assert imf._IMF_BASE == (
        "http://dataservices.imf.org/REST/SDMX_JSON/CompactData"
    )


def test_weo_countries_lists_eleven_expected_economies():
    countries = imf._WEO_COUNTRIES.split("+")
    expected = {
        "USA", "CHN", "DEU", "JPN", "KOR", "SGP",
        "NLD", "GBR", "FRA", "IND", "BRA",
    }
    assert set(countries) == expected
    assert len(countries) == len(expected)


def test_gdp_url_embeds_weo_countries_and_indicator():
    assert imf._GDP_URL.startswith(imf._IMF_BASE + "/WEO/")
    assert imf._WEO_COUNTRIES in imf._GDP_URL
    assert imf._GDP_URL.endswith("NGDP_RPCH")


def test_dots_url_embeds_dataset_and_indicators():
    assert "/DOT/" in imf._DOTS_URL
    assert "TXG_FOB_USD" in imf._DOTS_URL
    assert "TMG_CIF_USD" in imf._DOTS_URL
    assert imf._DOTS_URL.endswith("W00")


def test_commodity_url_embeds_pcps_and_codes():
    assert "/PCPS/" in imf._COMMODITY_URL
    for code in ("PCOAL", "PIRON", "PNGAS_US", "POILAPSP", "PSOYBEA", "PWHEAT"):
        assert code in imf._COMMODITY_URL


def test_commodity_labels_cover_every_pcps_code():
    assert set(imf._COMMODITY_LABELS) == {
        "PCOAL", "PIRON", "PNGAS_US", "POILAPSP", "PSOYBEA", "PWHEAT",
    }
    for code, label in imf._COMMODITY_LABELS.items():
        assert isinstance(label, str) and label, code


# ─── _fetch_url ────────────────────────────────────────────────────────────


def test_fetch_url_happy_path_returns_parsed_json(monkeypatch):
    payload = {"foo": "bar", "nested": {"x": 1}}

    def _fake_get(url, **kwargs):
        return _make_response(payload)

    monkeypatch.setattr(imf.requests, "get", _fake_get)
    assert imf._fetch_url("http://example/x", "test") == payload


def test_fetch_url_http_error_returns_empty(monkeypatch):
    def _fake_get(url, **kwargs):
        return _make_response({}, status=500)

    monkeypatch.setattr(imf.requests, "get", _fake_get)
    assert imf._fetch_url("http://example/x", "test") == {}


def test_fetch_url_timeout_returns_empty(monkeypatch):
    def _fake_get(url, **kwargs):
        raise requests.exceptions.Timeout("slow")

    monkeypatch.setattr(imf.requests, "get", _fake_get)
    assert imf._fetch_url("http://example/x", "test") == {}


def test_fetch_url_network_error_returns_empty(monkeypatch):
    def _fake_get(url, **kwargs):
        raise requests.exceptions.ConnectionError("offline")

    monkeypatch.setattr(imf.requests, "get", _fake_get)
    assert imf._fetch_url("http://example/x", "test") == {}


def test_fetch_url_unexpected_exception_returns_empty(monkeypatch):
    def _fake_get(url, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(imf.requests, "get", _fake_get)
    assert imf._fetch_url("http://example/x", "test") == {}


# ─── _cached_json ──────────────────────────────────────────────────────────


def test_cached_json_miss_then_hit_only_calls_fetch_once(tmp_path):
    cache = CacheManager(cache_dir=tmp_path)
    calls = {"n": 0}

    def _fetch() -> dict:
        calls["n"] += 1
        return {"hello": "world", "n": calls["n"]}

    first = imf._cached_json(cache, "k1", _fetch, ttl_hours=24.0)
    second = imf._cached_json(cache, "k1", _fetch, ttl_hours=24.0)

    assert first == {"hello": "world", "n": 1}
    assert second == first
    assert calls["n"] == 1


def test_cached_json_empty_payload_returns_empty_dict(tmp_path):
    cache = CacheManager(cache_dir=tmp_path)

    def _fetch_empty() -> dict:
        return {}

    out = imf._cached_json(cache, "empty", _fetch_empty, ttl_hours=24.0)
    assert out == {}


def test_cached_json_corrupt_cell_falls_back_to_empty(tmp_path, monkeypatch):
    """If something poisons the cached JSON column the helper must not raise."""
    cache = CacheManager(cache_dir=tmp_path)

    # Force get_or_fetch to return a frame whose 'json' cell is not valid JSON.
    bad_df = pd.DataFrame({"json": ["not-json-{"]})
    monkeypatch.setattr(
        cache, "get_or_fetch",
        lambda *a, **kw: bad_df, raising=True,
    )

    out = imf._cached_json(cache, "bad", lambda: {"x": 1}, ttl_hours=1.0)
    assert out == {}


# ─── _parse_imf_compact ────────────────────────────────────────────────────


def test_parse_compact_no_dataset_returns_empty():
    assert imf._parse_imf_compact({}) == {}
    assert imf._parse_imf_compact({"CompactData": {}}) == {}


def test_parse_compact_single_series_as_dict_is_accepted():
    """IMF returns a dict (not a list) when only one Series matches."""
    payload = {
        "CompactData": {
            "DataSet": {
                "Series": {
                    "@FREQ": "A",
                    "@REF_AREA": "USA",
                    "@INDICATOR": "NGDP_RPCH",
                    "Obs": [
                        {"@TIME_PERIOD": "2024", "@OBS_VALUE": "2.1"},
                        {"@TIME_PERIOD": "2025", "@OBS_VALUE": "2.3"},
                    ],
                },
            },
        },
    }
    out = imf._parse_imf_compact(payload)
    assert len(out) == 1
    [(key, series)] = out.items()
    assert "REF_AREA=USA" in key
    assert series == [("2024", 2.1), ("2025", 2.3)]


def test_parse_compact_single_obs_as_dict_is_accepted():
    payload = {
        "CompactData": {
            "DataSet": {
                "Series": [{
                    "@REF_AREA": "DEU",
                    "Obs": {"@TIME_PERIOD": "2025", "@OBS_VALUE": "1.5"},
                }],
            },
        },
    }
    out = imf._parse_imf_compact(payload)
    [(key, series)] = out.items()
    assert "REF_AREA=DEU" in key
    assert series == [("2025", 1.5)]


def test_parse_compact_series_key_concatenates_all_attrs_except_xmlns():
    payload = {
        "CompactData": {
            "DataSet": {
                "Series": [{
                    "@FREQ": "Q",
                    "@REF_AREA": "USA",
                    "@INDICATOR": "TXG_FOB_USD",
                    "@xmlns": "should-be-skipped",
                    "Obs": [{"@TIME_PERIOD": "2025-Q1", "@OBS_VALUE": "100"}],
                }],
            },
        },
    }
    out = imf._parse_imf_compact(payload)
    key = next(iter(out))
    assert "FREQ=Q" in key
    assert "REF_AREA=USA" in key
    assert "INDICATOR=TXG_FOB_USD" in key
    assert "xmlns" not in key


def test_parse_compact_non_numeric_obs_values_are_dropped():
    payload = {
        "CompactData": {
            "DataSet": {
                "Series": [{
                    "@REF_AREA": "USA",
                    "Obs": [
                        {"@TIME_PERIOD": "2024", "@OBS_VALUE": "1.5"},
                        {"@TIME_PERIOD": "2025", "@OBS_VALUE": "not-a-number"},
                        {"@TIME_PERIOD": "2026", "@OBS_VALUE": "2.0"},
                    ],
                }],
            },
        },
    }
    out = imf._parse_imf_compact(payload)
    [(_, series)] = out.items()
    assert series == [("2024", 1.5), ("2026", 2.0)]


def test_parse_compact_drops_obs_missing_either_field():
    payload = {
        "CompactData": {
            "DataSet": {
                "Series": [{
                    "@REF_AREA": "USA",
                    "Obs": [
                        {"@TIME_PERIOD": "2024"},               # missing value
                        {"@OBS_VALUE": "1.0"},                  # missing time
                        {"@TIME_PERIOD": "2025", "@OBS_VALUE": "2.5"},
                    ],
                }],
            },
        },
    }
    out = imf._parse_imf_compact(payload)
    [(_, series)] = out.items()
    assert series == [("2025", 2.5)]


def test_parse_compact_sorts_observations_by_time_ascending():
    payload = {
        "CompactData": {
            "DataSet": {
                "Series": [{
                    "@REF_AREA": "USA",
                    "Obs": [
                        {"@TIME_PERIOD": "2026", "@OBS_VALUE": "3.0"},
                        {"@TIME_PERIOD": "2024", "@OBS_VALUE": "1.0"},
                        {"@TIME_PERIOD": "2025", "@OBS_VALUE": "2.0"},
                    ],
                }],
            },
        },
    }
    out = imf._parse_imf_compact(payload)
    [(_, series)] = out.items()
    # Sorted ascending by time string so series[-1] is the latest.
    assert series == [("2024", 1.0), ("2025", 2.0), ("2026", 3.0)]


def test_parse_compact_non_dict_series_entries_are_skipped():
    payload = {
        "CompactData": {
            "DataSet": {
                "Series": [
                    "not-a-dict",
                    None,
                    {
                        "@REF_AREA": "USA",
                        "Obs": [{"@TIME_PERIOD": "2024", "@OBS_VALUE": "1.0"}],
                    },
                ],
            },
        },
    }
    out = imf._parse_imf_compact(payload)
    assert len(out) == 1


def test_parse_compact_series_with_no_valid_obs_is_dropped():
    payload = {
        "CompactData": {
            "DataSet": {
                "Series": [{
                    "@REF_AREA": "USA",
                    "Obs": [{"@TIME_PERIOD": "2024", "@OBS_VALUE": "nope"}],
                }],
            },
        },
    }
    assert imf._parse_imf_compact(payload) == {}


def test_parse_compact_returns_empty_on_unexpected_shape():
    # ``Series`` is something the function does not iterate cleanly.
    payload = {"CompactData": {"DataSet": {"Series": 12345}}}
    # The function returns {} (either from the broad except or the
    # non-dict skip path).
    assert imf._parse_imf_compact(payload) == {}


# ─── _extract_attr ────────────────────────────────────────────────────────


def test_extract_attr_returns_value_when_present():
    key = "FREQ=A|REF_AREA=USA|INDICATOR=NGDP_RPCH"
    assert imf._extract_attr(key, "REF_AREA") == "USA"
    assert imf._extract_attr(key, "FREQ") == "A"
    assert imf._extract_attr(key, "INDICATOR") == "NGDP_RPCH"


def test_extract_attr_returns_none_when_missing():
    key = "FREQ=A|REF_AREA=USA"
    assert imf._extract_attr(key, "INDICATOR") is None
    assert imf._extract_attr("", "FREQ") is None


def test_extract_attr_only_matches_exact_attribute_prefix():
    """``REF_AREA_X`` must not be matched by a request for ``REF_AREA``."""
    key = "REF_AREA_X=USA|REF_AREA=DEU"
    assert imf._extract_attr(key, "REF_AREA") == "DEU"


# ─── fetch_imf_data ────────────────────────────────────────────────────────


def test_fetch_imf_data_all_endpoints_empty_returns_empty_dict(monkeypatch, tmp_path):
    monkeypatch.setattr(imf, "CacheManager", lambda: CacheManager(cache_dir=tmp_path))
    _patch_fetch_url(monkeypatch, {})  # everything returns {}
    assert imf.fetch_imf_data(cache_ttl_hours=1.0) == {}


def test_fetch_imf_data_happy_path_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(imf, "CacheManager", lambda: CacheManager(cache_dir=tmp_path))
    gdp = _gdp_payload([
        ("USA", "2024", 2.1), ("USA", "2025", 2.3),
        ("CHN", "2024", 4.8), ("CHN", "2025", 4.5),
    ])
    dots = _dots_payload([
        ("US", "TXG_FOB_USD", "2025-Q1", 100.0),
        ("US", "TMG_CIF_USD", "2025-Q1",  80.0),
        ("CN", "TXG_FOB_USD", "2025-Q1", 200.0),
    ])
    pcps = _pcps_payload([
        ("PIRON",    "2025-01", 100.0),
        ("PIRON",    "2025-02", 105.0),
        ("PIRON",    "2025-03", 110.0),
        ("PIRON",    "2025-04", 115.0),
        ("POILAPSP", "2025-01",  70.0),
        ("POILAPSP", "2025-04",  80.0),  # only 2 points: 3m change stays 0
    ])
    _patch_fetch_url(monkeypatch, {
        "WEO GDP": gdp,
        "DOTS Trade": dots,
        "PCPS Commodities": pcps,
    })

    out = imf.fetch_imf_data(cache_ttl_hours=1.0)
    assert set(out) == {"gdp_forecasts", "trade_flows", "commodity_prices", "fetched_at"}

    # ISO-8601 with timezone offset (datetime.isoformat on a tz-aware dt).
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
        out["fetched_at"],
    )

    # GDP pinned to series[-1] (year sorted ascending).
    assert out["gdp_forecasts"] == {"USA": 2.3, "CHN": 4.5}

    # DOTS: TXG = exports, TMG = imports, dates filled.
    us_trade = out["trade_flows"]["US"]
    assert us_trade["exports_usd"] == 100.0
    assert us_trade["imports_usd"] == 80.0
    assert us_trade["latest_date"] == "2025-Q1"
    cn_trade = out["trade_flows"]["CN"]
    assert cn_trade["exports_usd"] == 200.0
    assert cn_trade["imports_usd"] is None  # only TXG row supplied

    # PCPS: commodity codes translated to labels, 3m change calc'd.
    iron = out["commodity_prices"]["Iron Ore"]
    assert iron["latest_price"] == 115.0
    assert iron["change_3m_pct"] == pytest.approx(15.0, abs=1e-6)
    oil = out["commodity_prices"]["Oil (Avg Spot)"]
    assert oil["latest_price"] == 80.0
    # Series too short for 3m comparison → stays 0.0
    assert oil["change_3m_pct"] == 0.0


def test_fetch_imf_data_zero_3m_base_does_not_divide_by_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(imf, "CacheManager", lambda: CacheManager(cache_dir=tmp_path))
    pcps = _pcps_payload([
        ("PCOAL", "2025-01", 0.0),     # zero 3m ago
        ("PCOAL", "2025-02", 1.0),
        ("PCOAL", "2025-03", 2.0),
        ("PCOAL", "2025-04", 3.0),
    ])
    _patch_fetch_url(monkeypatch, {"PCPS Commodities": pcps})

    out = imf.fetch_imf_data(cache_ttl_hours=1.0)
    coal = out["commodity_prices"]["Coal"]
    assert coal["latest_price"] == 3.0
    # Division-by-zero guard: change stays at the default 0.0.
    assert coal["change_3m_pct"] == 0.0


def test_fetch_imf_data_unknown_commodity_code_uses_code_as_label(monkeypatch, tmp_path):
    monkeypatch.setattr(imf, "CacheManager", lambda: CacheManager(cache_dir=tmp_path))
    pcps = _pcps_payload([("PUNKNOWN", "2025-04", 12.345)])
    _patch_fetch_url(monkeypatch, {"PCPS Commodities": pcps})

    out = imf.fetch_imf_data(cache_ttl_hours=1.0)
    # Falls back to raw code when not in _COMMODITY_LABELS.
    assert "PUNKNOWN" in out["commodity_prices"]
    assert out["commodity_prices"]["PUNKNOWN"]["latest_price"] == pytest.approx(12.345)


def test_fetch_imf_data_dots_latest_date_filled_by_tmg_when_no_txg(monkeypatch, tmp_path):
    monkeypatch.setattr(imf, "CacheManager", lambda: CacheManager(cache_dir=tmp_path))
    dots = _dots_payload([
        ("DE", "TMG_CIF_USD", "2024-Q4", 50.0),
    ])
    _patch_fetch_url(monkeypatch, {"DOTS Trade": dots})

    out = imf.fetch_imf_data(cache_ttl_hours=1.0)
    de = out["trade_flows"]["DE"]
    assert de["imports_usd"] == 50.0
    assert de["exports_usd"] is None
    # When only TMG is seen, latest_date should still be populated.
    assert de["latest_date"] == "2024-Q4"


# ─── get_shipping_demand_outlook ───────────────────────────────────────────


def test_outlook_empty_input_returns_static_defaults():
    out = imf.get_shipping_demand_outlook({})
    assert out["global_gdp_signal"] == "Stable"
    assert out["commodity_shipping_demand"] == "Moderate"
    assert out["iron_ore_trend"] == "Stable"
    assert out["oil_trend"] == "Stable"
    assert out["top_growth_economies"] == []
    assert out["demand_score"] == 0.5


def test_outlook_top_growth_sorted_descending_and_capped_at_three():
    data = {
        "gdp_forecasts": {
            "USA": 2.0,
            "IND": 6.5,
            "CHN": 4.8,
            "DEU": 0.5,
            "BRA": 3.0,
        },
    }
    out = imf.get_shipping_demand_outlook(data)
    # IND > CHN > BRA > USA > DEU — top 3 in order.
    assert out["top_growth_economies"] == ["IND", "CHN", "BRA"]


def test_outlook_accelerating_when_avg_growth_above_threshold():
    data = {"gdp_forecasts": {"A": 4.0, "B": 4.0, "C": 4.0}}
    out = imf.get_shipping_demand_outlook(data)
    assert out["global_gdp_signal"] == "Accelerating"


def test_outlook_decelerating_when_avg_growth_below_threshold():
    data = {"gdp_forecasts": {"A": 1.0, "B": 1.5}}
    out = imf.get_shipping_demand_outlook(data)
    assert out["global_gdp_signal"] == "Decelerating"


def test_outlook_stable_when_avg_growth_between_thresholds():
    # Avg = 2.5: above the 2.0 decelerating bound, below the 3.5
    # accelerating bound.
    data = {"gdp_forecasts": {"A": 2.5}}
    out = imf.get_shipping_demand_outlook(data)
    assert out["global_gdp_signal"] == "Stable"


def test_outlook_iron_oil_trend_thresholds():
    data = {
        "commodity_prices": {
            "Iron Ore":       {"latest_price": 100, "change_3m_pct":  5.0},   # Rising
            "Oil (Avg Spot)": {"latest_price":  80, "change_3m_pct": -5.0},   # Falling
        },
    }
    out = imf.get_shipping_demand_outlook(data)
    assert out["iron_ore_trend"] == "Rising"
    assert out["oil_trend"] == "Falling"


def test_outlook_trend_stable_inside_pm_two_percent_band():
    data = {
        "commodity_prices": {
            "Iron Ore":       {"change_3m_pct":  1.5},
            "Oil (Avg Spot)": {"change_3m_pct": -1.5},
        },
    }
    out = imf.get_shipping_demand_outlook(data)
    assert out["iron_ore_trend"] == "Stable"
    assert out["oil_trend"] == "Stable"


def test_outlook_bulk_avg_above_three_signals_high_demand():
    data = {
        "commodity_prices": {
            "Iron Ore":   {"change_3m_pct":  5.0},
            "Coal":       {"change_3m_pct":  4.0},
            "Soybeans":   {"change_3m_pct":  6.0},
            "Wheat":      {"change_3m_pct":  5.0},
        },
    }
    out = imf.get_shipping_demand_outlook(data)
    assert out["commodity_shipping_demand"] == "High"


def test_outlook_bulk_avg_below_minus_three_signals_low_demand():
    data = {
        "commodity_prices": {
            "Iron Ore":   {"change_3m_pct": -5.0},
            "Coal":       {"change_3m_pct": -4.0},
            "Soybeans":   {"change_3m_pct": -6.0},
            "Wheat":      {"change_3m_pct": -5.0},
        },
    }
    out = imf.get_shipping_demand_outlook(data)
    assert out["commodity_shipping_demand"] == "Low"


def test_outlook_bulk_avg_in_band_is_moderate():
    data = {
        "commodity_prices": {
            "Iron Ore":   {"change_3m_pct":  1.0},
            "Coal":       {"change_3m_pct": -1.0},
            "Soybeans":   {"change_3m_pct":  0.0},
            "Wheat":      {"change_3m_pct":  0.5},
        },
    }
    out = imf.get_shipping_demand_outlook(data)
    assert out["commodity_shipping_demand"] == "Moderate"


def test_outlook_demand_score_blends_components_and_is_rounded():
    data = {
        # avg growth 6.0 → gdp component = min(1.0, 6/6) = 1.0
        "gdp_forecasts": {"A": 6.0, "B": 6.0},
        "commodity_prices": {
            # Bulk avg = 5.0 → High → commodity_map = 0.9
            "Iron Ore":   {"change_3m_pct": 5.0},
            "Coal":       {"change_3m_pct": 5.0},
            "Soybeans":   {"change_3m_pct": 5.0},
            "Wheat":      {"change_3m_pct": 5.0},
            # Iron rising → 0.8
            "Oil (Avg Spot)": {"change_3m_pct": 0.0},
        },
    }
    out = imf.get_shipping_demand_outlook(data)
    expected = round((1.0 + 0.9 + 0.8) / 3, 3)
    assert out["demand_score"] == expected
    # Always within [0, 1] and rounded to ≤ 3 dp.
    assert 0.0 <= out["demand_score"] <= 1.0


def test_outlook_demand_score_caps_gdp_component_at_one():
    """avg growth of 20 should not let the GDP component exceed 1.0."""
    data = {"gdp_forecasts": {"A": 20.0}}
    out = imf.get_shipping_demand_outlook(data)
    # gdp=1.0, commodity moderate→0.5, iron stable→0.5
    assert out["demand_score"] == pytest.approx(round((1.0 + 0.5 + 0.5) / 3, 3))


def test_outlook_demand_score_floor_at_zero_for_negative_growth():
    """avg growth of -5 should clamp the GDP component to 0.0."""
    data = {"gdp_forecasts": {"A": -5.0}}
    out = imf.get_shipping_demand_outlook(data)
    # gdp=0.0, commodity moderate→0.5, iron stable→0.5
    assert out["demand_score"] == pytest.approx(round((0.0 + 0.5 + 0.5) / 3, 3))
