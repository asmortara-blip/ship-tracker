"""Tests for data.oecd_feed.

The OECD feed is a thin SDMX-JSON parser plus a three-endpoint orchestrator
(CLI / Trade / Industrial Production) sitting on top of CacheManager. The
goal here is to pin every pure code path that does not touch the network.

All requests are mocked. CacheManager.get_or_fetch is replaced with a direct
pass-through so we exercise the actual orchestration logic without a real
parquet cache. No live OECD endpoint is ever called.

Covers:
  - Module-level: endpoint URLs use the canonical SDMX-JSON base and the
    expected MEI/MEI_CLI/MEI_TRADE flow datasets, with allDimensions on the
    observation axis (so the parser can rely on a colon-encoded key).

  - _parse_sdmx_json (pure helper, no network):
      * Empty / None / non-dict payload → {} (caught by outer try/except).
      * Missing 'structure' or 'dataSets' → {} (graceful).
      * Single-series happy path: observations decoded, sorted by date,
        non-time dimensions concatenated into the series key.
      * Two-dim with explicit role="time" on the last axis → time picked up
        even when the dim id is not "TIME_PERIOD".
      * Time dim id containing the substring "TIME" picks up time index.
      * Time defaults to last dimension when role/id give no hint.
      * Observation with malformed key length (index count != dim count)
        is skipped (rest of the payload still parses).
      * Observation with non-numeric value is skipped.
      * Observation with non-integer index string is skipped.
      * Out-of-range dimension index falls back to the raw index string in
        the series key (not raised); out-of-range time index → series row
        dropped.
      * obs_vals empty or [None] → row dropped, no float() crash.
      * Result is sorted by date string ascending per series.

  - _fetch_url:
      * Happy path → resp.json() returned.
      * Timeout, HTTPError, generic RequestException, unexpected Exception
        all return {} (never raise).

  - fetch_oecd_indicators (orchestrator):
      * All three endpoints empty → top-level returns {} (not a dict with
        empty children).
      * CLI-only payload populates 'cli' with composite keys
        '<country>:<indicator>' and leaves trade / IP empty.
      * Trade payload routes XTIMVA01 → imports, XTEXVA01 → exports per
        country; unknown indicators leave both lists untouched.
      * IP payload picks the country from position 1 of the series key.
      * cache_ttl_hours is forwarded to CacheManager.get_or_fetch.
      * fetched_at is an ISO-formatted UTC timestamp.

  - get_global_trade_momentum (derived signal):
      * Empty input → defaults dict with all four neutral values.
      * US trade growth uses imports first, falls back to exports when
        imports list is empty, returns 0.0 when prior value is 0.
      * China IP trend: large positive diff → "Expanding"; large negative
        diff → "Contracting"; small diff → "Stable"; short series → "Stable".
      * EU CLI signal: DEU > 100.2 → "Positive"; < 99.8 → "Negative";
        in-band → "Neutral"; missing DEU → "Neutral".
      * Asia demand score: empty IP dict → 0.5; ±5% maps to 0–1 with 0
        change → 0.5 mid; clamps to [0, 1] for large changes; zero earlier
        avg short-circuits to 0 change for that country (no DivisionByZero).
"""
from __future__ import annotations

from datetime import datetime

import pytest
import requests

from data import oecd_feed
from data.oecd_feed import (
    _CLI_URL,
    _IP_URL,
    _OECD_BASE,
    _TRADE_URL,
    _fetch_url,
    _parse_sdmx_json,
    fetch_oecd_indicators,
    get_global_trade_momentum,
)


# ─── Helpers ───────────────────────────────────────────────────────────────


def _sdmx_payload(
    dim_specs: list[dict],
    observations: dict[str, list],
    time_role: bool = True,
    time_id: str = "TIME_PERIOD",
) -> dict:
    """Build a minimal SDMX-JSON payload.

    dim_specs is a list of {"id": str, "values": [str, ...]} for each
    observation dimension. The last dimension is treated as the time axis
    when time_role=True (role="time"); otherwise the parser will fall back
    to id-substring or last-position detection.
    """
    dims = []
    for i, spec in enumerate(dim_specs):
        d: dict = {
            "id": spec["id"],
            "values": [{"id": v} for v in spec["values"]],
        }
        # Mark last dimension as time when requested
        if time_role and i == len(dim_specs) - 1:
            d["role"] = "time"
            d["keyPosition"] = i
        dims.append(d)

    return {
        "structure": {"dimensions": {"observation": dims}},
        "dataSets": [{"observations": observations}],
    }


class _FakeResponse:
    """Drop-in for requests.Response for the small surface _fetch_url uses."""

    def __init__(self, *, json_payload=None, status_code: int = 200,
                 raise_on_status: Exception | None = None):
        self._json = json_payload
        self.status_code = status_code
        self._raise = raise_on_status

    def raise_for_status(self) -> None:
        if self._raise is not None:
            raise self._raise

    def json(self):
        return self._json


def _patch_passthrough_cache(monkeypatch):
    """Replace CacheManager.get_or_fetch with a direct pass-through.

    The OECD module calls cache.get_or_fetch(key, fetch_fn, ttl_hours=..., source=...),
    so the wrapper accepts that positional/kwarg shape and records the ttl.
    """
    seen: dict = {"ttl_hours": [], "keys": []}

    def _passthrough(self, key, fetch_fn, ttl_hours, source="misc"):
        seen["ttl_hours"].append(ttl_hours)
        seen["keys"].append(key)
        return fetch_fn()

    monkeypatch.setattr(
        "data.cache_manager.CacheManager.get_or_fetch",
        _passthrough,
    )
    return seen


# ─── Module-level URL sanity ───────────────────────────────────────────────


def test_module_urls_use_canonical_oecd_base():
    """All three endpoint URLs are rooted at the SDMX-JSON service."""
    assert _OECD_BASE == "https://stats.oecd.org/SDMX-JSON/data"
    for url in (_CLI_URL, _TRADE_URL, _IP_URL):
        assert url.startswith(_OECD_BASE + "/")


def test_module_urls_request_all_dimensions():
    """allDimensions is required for the parser's colon-encoded keys."""
    for url in (_CLI_URL, _TRADE_URL, _IP_URL):
        assert "dimensionAtObservation=allDimensions" in url


def test_module_urls_target_expected_datasets():
    """CLI/Trade/IP each target their own MEI flow."""
    assert "/MEI_CLI/" in _CLI_URL
    assert "/MEI_TRADE/" in _TRADE_URL
    assert "/MEI/" in _IP_URL


# ─── _parse_sdmx_json ──────────────────────────────────────────────────────


def test_parse_sdmx_empty_dict_returns_empty():
    assert _parse_sdmx_json({}) == {}


def test_parse_sdmx_missing_structure_returns_empty():
    assert _parse_sdmx_json({"dataSets": [{}]}) == {}


def test_parse_sdmx_missing_datasets_returns_empty():
    payload = {"structure": {"dimensions": {"observation": [
        {"id": "TIME_PERIOD", "values": [{"id": "2025-01"}], "role": "time"}
    ]}}}
    out = _parse_sdmx_json(payload)
    assert out == {}


def test_parse_sdmx_non_dict_returns_empty():
    """Outer try/except catches AttributeError when .get is missing."""
    assert _parse_sdmx_json(None) == {}  # type: ignore[arg-type]
    assert _parse_sdmx_json([]) == {}    # type: ignore[arg-type]


def test_parse_sdmx_happy_path_two_dims():
    """Two dims (country, time) with two observations parse cleanly."""
    payload = _sdmx_payload(
        dim_specs=[
            {"id": "LOCATION", "values": ["USA", "DEU"]},
            {"id": "TIME_PERIOD", "values": ["2025-01", "2025-02", "2025-03"]},
        ],
        observations={
            "0:0": [100.0],
            "0:1": [101.5],
            "0:2": [102.3],
            "1:0": [98.0],
            "1:1": [99.2],
        },
    )
    out = _parse_sdmx_json(payload)

    # Series keyed by all non-time dimensions
    assert "USA" in out
    assert "DEU" in out
    # Sorted ascending by date
    assert out["USA"] == [
        ("2025-01", 100.0),
        ("2025-02", 101.5),
        ("2025-03", 102.3),
    ]
    assert out["DEU"] == [("2025-01", 98.0), ("2025-02", 99.2)]


def test_parse_sdmx_three_dims_concatenates_non_time_parts():
    """Indicator + Country compose the series key, time is excluded."""
    payload = _sdmx_payload(
        dim_specs=[
            {"id": "INDICATOR", "values": ["LOLITOAA"]},
            {"id": "LOCATION", "values": ["USA", "JPN"]},
            {"id": "TIME_PERIOD", "values": ["2025-01", "2025-02"]},
        ],
        observations={
            "0:0:0": [100.5],
            "0:0:1": [101.0],
            "0:1:0": [99.0],
        },
    )
    out = _parse_sdmx_json(payload)

    assert "LOLITOAA:USA" in out
    assert "LOLITOAA:JPN" in out
    assert out["LOLITOAA:USA"] == [("2025-01", 100.5), ("2025-02", 101.0)]


def test_parse_sdmx_time_dim_by_role_only():
    """Even with id='Foo' and role='time', the last position is treated as time."""
    payload = _sdmx_payload(
        dim_specs=[
            {"id": "LOC", "values": ["USA"]},
            {"id": "PERIOD_OF_TIME", "values": ["2025-01", "2025-02"]},
        ],
        observations={"0:0": [1.0], "0:1": [2.0]},
        time_role=True,
    )
    out = _parse_sdmx_json(payload)
    assert out == {"USA": [("2025-01", 1.0), ("2025-02", 2.0)]}


def test_parse_sdmx_time_dim_falls_back_to_last_position():
    """No role/keyPosition hints → time_dim_index defaults to last dim."""
    payload = {
        "structure": {"dimensions": {"observation": [
            {"id": "COUNTRY", "values": [{"id": "USA"}]},
            {"id": "PERIOD", "values": [{"id": "2025-01"}, {"id": "2025-02"}]},
        ]}},
        "dataSets": [{"observations": {"0:0": [10.0], "0:1": [20.0]}}],
    }
    out = _parse_sdmx_json(payload)
    assert out == {"USA": [("2025-01", 10.0), ("2025-02", 20.0)]}


def test_parse_sdmx_skips_observation_with_wrong_index_count():
    """Observation key with too few/many indices is silently skipped."""
    payload = _sdmx_payload(
        dim_specs=[
            {"id": "LOC", "values": ["USA"]},
            {"id": "TIME_PERIOD", "values": ["2025-01"]},
        ],
        observations={
            "0:0": [42.0],         # well-formed
            "0:0:0": [99.0],       # too many indices
            "0": [77.0],           # too few indices
        },
    )
    out = _parse_sdmx_json(payload)
    assert out == {"USA": [("2025-01", 42.0)]}


def test_parse_sdmx_skips_non_numeric_value():
    payload = _sdmx_payload(
        dim_specs=[
            {"id": "LOC", "values": ["USA"]},
            {"id": "TIME_PERIOD", "values": ["2025-01", "2025-02"]},
        ],
        observations={
            "0:0": ["not a number"],
            "0:1": [3.14],
        },
    )
    out = _parse_sdmx_json(payload)
    assert out == {"USA": [("2025-02", 3.14)]}


def test_parse_sdmx_skips_empty_obs_vals():
    payload = _sdmx_payload(
        dim_specs=[
            {"id": "LOC", "values": ["USA"]},
            {"id": "TIME_PERIOD", "values": ["2025-01", "2025-02"]},
        ],
        observations={
            "0:0": [],         # empty → value=None → skip
            "0:1": [None],     # explicit None → skip
        },
    )
    out = _parse_sdmx_json(payload)
    assert out == {}


def test_parse_sdmx_skips_non_integer_index_in_part():
    """ValueError on int(idx_str) → that index is skipped, others still parse."""
    payload = _sdmx_payload(
        dim_specs=[
            {"id": "LOC", "values": ["USA", "JPN"]},
            {"id": "TIME_PERIOD", "values": ["2025-01"]},
        ],
        observations={
            "x:0": [1.0],   # 'x' fails int() → both parts skipped; date None → drop
            "1:0": [9.0],   # well-formed
        },
    )
    out = _parse_sdmx_json(payload)
    assert "JPN" in out
    assert out["JPN"] == [("2025-01", 9.0)]


def test_parse_sdmx_sorts_each_series_ascending_by_date():
    """Observations enumerate in any order; output dates are sorted ascending."""
    payload = _sdmx_payload(
        dim_specs=[
            {"id": "LOC", "values": ["USA"]},
            {"id": "TIME_PERIOD", "values": ["2025-03", "2025-01", "2025-02"]},
        ],
        observations={
            "0:0": [3.0],   # 2025-03
            "0:1": [1.0],   # 2025-01
            "0:2": [2.0],   # 2025-02
        },
    )
    out = _parse_sdmx_json(payload)
    # Sorted lexicographically — ISO dates sort correctly as strings
    assert out["USA"] == [
        ("2025-01", 1.0),
        ("2025-02", 2.0),
        ("2025-03", 3.0),
    ]


# ─── _fetch_url ────────────────────────────────────────────────────────────


def test_fetch_url_happy_path_returns_json(monkeypatch):
    payload = {"structure": {"dimensions": {"observation": []}}}
    monkeypatch.setattr(
        oecd_feed.requests,
        "get",
        lambda url, timeout, headers: _FakeResponse(json_payload=payload),
    )
    out = _fetch_url("http://example/whatever", "CLI")
    assert out == payload


def test_fetch_url_timeout_returns_empty(monkeypatch):
    def _raise(*a, **kw):
        raise requests.exceptions.Timeout("slow")
    monkeypatch.setattr(oecd_feed.requests, "get", _raise)
    assert _fetch_url("http://example", "CLI") == {}


def test_fetch_url_http_error_returns_empty(monkeypatch):
    err = requests.exceptions.HTTPError("nope")
    # Attach a synthetic response so the handler's exc.response.status_code works.
    err.response = _FakeResponse(status_code=503)

    def _raise(*a, **kw):
        return _FakeResponse(raise_on_status=err)

    monkeypatch.setattr(oecd_feed.requests, "get", _raise)
    assert _fetch_url("http://example", "Trade") == {}


def test_fetch_url_request_exception_returns_empty(monkeypatch):
    def _raise(*a, **kw):
        raise requests.exceptions.RequestException("dns explode")
    monkeypatch.setattr(oecd_feed.requests, "get", _raise)
    assert _fetch_url("http://example", "IP") == {}


def test_fetch_url_unexpected_error_returns_empty(monkeypatch):
    def _raise(*a, **kw):
        raise ValueError("totally unexpected")
    monkeypatch.setattr(oecd_feed.requests, "get", _raise)
    assert _fetch_url("http://example", "CLI") == {}


# ─── fetch_oecd_indicators (orchestrator) ──────────────────────────────────


def test_fetch_oecd_indicators_all_empty_returns_empty(monkeypatch):
    """If all three endpoints come back empty, the orchestrator returns {}."""
    _patch_passthrough_cache(monkeypatch)
    monkeypatch.setattr(oecd_feed, "_fetch_url", lambda url, label: {})
    assert fetch_oecd_indicators(cache_ttl_hours=1.0) == {}


def test_fetch_oecd_indicators_cli_only_populates_cli(monkeypatch):
    """A CLI-only payload yields a non-empty 'cli', with empty trade & IP."""
    _patch_passthrough_cache(monkeypatch)

    cli_payload = _sdmx_payload(
        dim_specs=[
            {"id": "INDICATOR", "values": ["LOLITOAA"]},
            {"id": "LOCATION", "values": ["USA", "DEU"]},
            {"id": "TIME_PERIOD", "values": ["2025-01", "2025-02"]},
        ],
        observations={
            "0:0:0": [100.1],
            "0:0:1": [100.5],
            "0:1:0": [99.8],
        },
    )

    def _route(url, label):
        if "MEI_CLI" in url:
            return cli_payload
        return {}

    monkeypatch.setattr(oecd_feed, "_fetch_url", _route)

    out = fetch_oecd_indicators(cache_ttl_hours=1.0)

    assert out  # non-empty top-level
    # Composite keys: country:indicator
    assert "USA:LOLITOAA" in out["cli"]
    assert "DEU:LOLITOAA" in out["cli"]
    assert out["cli"]["USA:LOLITOAA"] == [("2025-01", 100.1), ("2025-02", 100.5)]
    assert out["trade"] == {}
    assert out["industrial_production"] == {}


def test_fetch_oecd_indicators_trade_routes_imports_and_exports(monkeypatch):
    """XTIMVA01 → imports, XTEXVA01 → exports per country."""
    _patch_passthrough_cache(monkeypatch)

    trade_payload = _sdmx_payload(
        dim_specs=[
            {"id": "INDICATOR", "values": ["XTIMVA01", "XTEXVA01", "OTHER01"]},
            {"id": "LOCATION", "values": ["USA"]},
            {"id": "MEASURE", "values": ["GP"]},
            {"id": "TIME_PERIOD", "values": ["2025-01", "2025-02"]},
        ],
        observations={
            "0:0:0:0": [200.0],   # USA imports 2025-01
            "0:0:0:1": [210.0],   # USA imports 2025-02
            "1:0:0:0": [180.0],   # USA exports 2025-01
            "2:0:0:0": [50.0],    # OTHER indicator — ignored by router
        },
    )

    def _route(url, label):
        if "MEI_TRADE" in url:
            return trade_payload
        return {}

    monkeypatch.setattr(oecd_feed, "_fetch_url", _route)

    out = fetch_oecd_indicators(cache_ttl_hours=1.0)

    assert "USA" in out["trade"]
    usa = out["trade"]["USA"]
    assert usa["imports"] == [("2025-01", 200.0), ("2025-02", 210.0)]
    assert usa["exports"] == [("2025-01", 180.0)]


def test_fetch_oecd_indicators_ip_uses_country_position_one(monkeypatch):
    """IP series key picks index 1 (country) as the dict key."""
    _patch_passthrough_cache(monkeypatch)

    ip_payload = _sdmx_payload(
        dim_specs=[
            {"id": "INDICATOR", "values": ["PRINTO01"]},
            {"id": "LOCATION", "values": ["CHN", "USA"]},
            {"id": "MEASURE", "values": ["IXOBSA"]},
            {"id": "TIME_PERIOD", "values": ["2025-01", "2025-02"]},
        ],
        observations={
            "0:0:0:0": [102.0],
            "0:0:0:1": [103.5],
            "0:1:0:0": [99.0],
        },
    )

    def _route(url, label):
        # The IP URL path uses "/MEI/" exactly (CLI uses /MEI_CLI/, Trade /MEI_TRADE/).
        if "/MEI/" in url and "MEI_CLI" not in url and "MEI_TRADE" not in url:
            return ip_payload
        return {}

    monkeypatch.setattr(oecd_feed, "_fetch_url", _route)

    out = fetch_oecd_indicators(cache_ttl_hours=1.0)

    assert "CHN" in out["industrial_production"]
    assert "USA" in out["industrial_production"]
    assert out["industrial_production"]["CHN"] == [
        ("2025-01", 102.0),
        ("2025-02", 103.5),
    ]


def test_fetch_oecd_indicators_forwards_cache_ttl(monkeypatch):
    """cache_ttl_hours flows through to CacheManager.get_or_fetch (3 calls)."""
    seen = _patch_passthrough_cache(monkeypatch)
    monkeypatch.setattr(oecd_feed, "_fetch_url", lambda url, label: {})

    fetch_oecd_indicators(cache_ttl_hours=72.0)

    # One call each for CLI / Trade / IP
    assert seen["ttl_hours"] == [72.0, 72.0, 72.0]
    assert set(seen["keys"]) == {"cli_mei_cli", "trade_mei_trade", "ip_mei"}


def test_fetch_oecd_indicators_fetched_at_is_iso_utc(monkeypatch):
    """fetched_at is a parseable ISO timestamp."""
    _patch_passthrough_cache(monkeypatch)

    cli_payload = _sdmx_payload(
        dim_specs=[
            {"id": "INDICATOR", "values": ["LOLITOAA"]},
            {"id": "LOCATION", "values": ["USA"]},
            {"id": "TIME_PERIOD", "values": ["2025-01"]},
        ],
        observations={"0:0:0": [100.0]},
    )

    def _route(url, label):
        return cli_payload if "MEI_CLI" in url else {}

    monkeypatch.setattr(oecd_feed, "_fetch_url", _route)

    out = fetch_oecd_indicators(cache_ttl_hours=1.0)

    assert "fetched_at" in out
    # ISO with timezone — datetime.fromisoformat should accept it
    ts = datetime.fromisoformat(out["fetched_at"])
    assert ts.tzinfo is not None


# ─── get_global_trade_momentum ─────────────────────────────────────────────


def _series(values: list[float], start_month: int = 1) -> list[tuple[str, float]]:
    """Build a (date, value) series with monthly dates starting at 2025-MM."""
    out = []
    for i, v in enumerate(values):
        m = start_month + i
        year = 2025 + (m - 1) // 12
        month = ((m - 1) % 12) + 1
        out.append((f"{year:04d}-{month:02d}", float(v)))
    return out


def test_momentum_empty_input_returns_defaults():
    out = get_global_trade_momentum({})
    assert out == {
        "us_trade_growth_3m": 0.0,
        "china_ip_trend": "Stable",
        "eu_cli_signal": "Neutral",
        "asia_demand_score": 0.5,
    }


def test_momentum_us_trade_growth_uses_imports():
    """Imports preferred; growth is (recent - prior_3m) / |prior_3m| * 100."""
    data = {
        "trade": {
            "USA": {
                # 4+ points so series[-1] and series[-4] are distinct
                "imports": _series([100.0, 105.0, 110.0, 121.0]),
                "exports": _series([1.0, 2.0, 3.0, 4.0]),
            }
        },
    }
    out = get_global_trade_momentum(data)
    # (121 - 100)/100 * 100 = 21.0
    assert out["us_trade_growth_3m"] == 21.0


def test_momentum_us_trade_falls_back_to_exports_when_imports_empty():
    """Empty imports list → orchestrator uses exports instead."""
    data = {
        "trade": {
            "USA": {
                "imports": [],
                "exports": _series([50.0, 52.0, 55.0, 60.0]),
            }
        }
    }
    out = get_global_trade_momentum(data)
    # (60 - 50)/50 * 100 = 20.0
    assert out["us_trade_growth_3m"] == 20.0


def test_momentum_us_trade_zero_prior_returns_zero():
    data = {
        "trade": {
            "USA": {
                "imports": _series([0.0, 1.0, 2.0, 3.0]),
                "exports": [],
            }
        }
    }
    out = get_global_trade_momentum(data)
    assert out["us_trade_growth_3m"] == 0.0


def test_momentum_china_ip_expanding():
    """Recent 3-month avg minus earlier 3-month avg > 0.5 → Expanding."""
    # earlier_avg = (100+101+102)/3 = 101 ; recent_avg = (104+105+106)/3 = 105
    data = {
        "industrial_production": {
            "CHN": _series([100.0, 101.0, 102.0, 104.0, 105.0, 106.0])
        }
    }
    out = get_global_trade_momentum(data)
    assert out["china_ip_trend"] == "Expanding"


def test_momentum_china_ip_contracting():
    # earlier_avg = 105, recent_avg = 100 → diff = -5 → Contracting
    data = {
        "industrial_production": {
            "CHN": _series([104.0, 105.0, 106.0, 100.0, 100.0, 100.0])
        }
    }
    out = get_global_trade_momentum(data)
    assert out["china_ip_trend"] == "Contracting"


def test_momentum_china_ip_stable_when_diff_small():
    # earlier_avg ≈ recent_avg → |diff| <= 0.5 → Stable
    data = {
        "industrial_production": {
            "CHN": _series([100.0, 100.1, 100.2, 100.3, 100.2, 100.1])
        }
    }
    out = get_global_trade_momentum(data)
    assert out["china_ip_trend"] == "Stable"


def test_momentum_china_ip_stable_when_series_short():
    # Only 3 points — branch requires >= 4, so default 'Stable' stays
    data = {"industrial_production": {"CHN": _series([100.0, 105.0, 110.0])}}
    assert get_global_trade_momentum(data)["china_ip_trend"] == "Stable"


def test_momentum_eu_cli_positive():
    data = {"cli": {"DEU:LOLITOAA": _series([100.0, 100.1, 101.5])}}
    assert get_global_trade_momentum(data)["eu_cli_signal"] == "Positive"


def test_momentum_eu_cli_negative():
    data = {"cli": {"DEU:LOLITOAA": _series([100.0, 99.9, 99.5])}}
    assert get_global_trade_momentum(data)["eu_cli_signal"] == "Negative"


def test_momentum_eu_cli_neutral_when_in_band():
    data = {"cli": {"DEU:LOLITOAA": _series([100.0, 100.0, 100.0])}}
    assert get_global_trade_momentum(data)["eu_cli_signal"] == "Neutral"


def test_momentum_eu_cli_neutral_when_no_deu():
    data = {"cli": {"USA:LOLITOAA": _series([100.0, 105.0, 110.0])}}
    assert get_global_trade_momentum(data)["eu_cli_signal"] == "Neutral"


def test_momentum_asia_demand_empty_returns_default():
    """No IP data → score stays at default 0.5."""
    assert get_global_trade_momentum({"cli": {}, "trade": {}, "industrial_production": {}})[
        "asia_demand_score"
    ] == 0.5


def test_momentum_asia_demand_score_in_unit_interval():
    """Score is always clamped to [0, 1] and is the mean across CHN/JPN/KOR."""
    # CHN: flat → 0.5; JPN: huge jump → clamps to 1.0; KOR: huge drop → clamps to 0.0
    data = {
        "industrial_production": {
            "CHN": _series([100.0] * 6),
            "JPN": _series([100.0, 100.0, 100.0, 200.0, 200.0, 200.0]),
            "KOR": _series([200.0, 200.0, 200.0, 50.0, 50.0, 50.0]),
        }
    }
    out = get_global_trade_momentum(data)
    score = out["asia_demand_score"]
    assert 0.0 <= score <= 1.0
    # mean of [0.5, 1.0, 0.0] = 0.5
    assert score == pytest.approx(0.5)


def test_momentum_asia_demand_handles_zero_earlier_avg_without_crash():
    """earlier_avg = 0 short-circuits to change_pct = 0 → score = 0.5."""
    data = {
        "industrial_production": {
            "CHN": _series([0.0, 0.0, 0.0, 10.0, 10.0, 10.0]),
        }
    }
    out = get_global_trade_momentum(data)
    # Single country, zero earlier_avg → change_pct = 0 → score = 0.5
    assert out["asia_demand_score"] == pytest.approx(0.5)
