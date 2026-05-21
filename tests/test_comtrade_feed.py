"""Tests for ``data.comtrade_feed``.

Despite the legacy module name, this feed no longer talks to UN Comtrade. It
fetches country-level trade flows from two World-Bank-hosted endpoints
(neither requires an API key):

  1. **WITS** ``tradeStats/tradestats-trade`` — preferred, gives HS-4 level
     trade values per country / year / flow direction.
  2. **World Bank v2 merchandise indicators** — fallback used when WITS
     returns nothing for every tracked country.

The module then projects country-level numbers down onto our internal port
roster using the ``PORT_TRAFFIC_WEIGHTS`` table and synthesises a per-country
HS-code mix from ``_COUNTRY_CATEGORY_SHARES`` whenever the WITS fallback
trips.

Every network call is mocked via ``monkeypatch``; no real WITS / WB requests
are made. The two HTTP-touching entry points are wrapped:

  * ``fetch_all_ports`` — ``@st.cache_data`` → unwrap via ``.__wrapped__``
    *and* ``.clear()`` between tests so the in-process memo never bleeds.
  * ``_fetch_wits_country`` — ``@tenacity.retry`` → unwrap via
    ``.__wrapped__`` so failure-path tests don't pay backoff time.

Covers:
  * ``_ISO3_TO_ISO2`` — every alpha-3 maps to a 2-letter alpha-2.
  * ``_HS_CATEGORY_MAP`` — every shipped HS-4 is exactly 4 chars and maps
    to a category that exists in ``_CATEGORY_SHARES``.
  * ``_CATEGORY_SHARES`` — sums to ~1.0; same categories as the per-country
    tables.
  * ``_COUNTRY_CATEGORY_SHARES`` — every row matches the global category
    set; no row is silently empty.
  * ``_category_shares_for_country`` — known country returns its
    hand-tuned mix normalised to 1.0, unknown country falls back to
    ``_CATEGORY_SHARES`` (also normalised), values stay non-negative.
  * ``_fetch_wits_country`` —
      - builds ``{base}/{iso2}/{year}/all/{flow}`` URLs for both flows;
      - parses the ``{"TradeStats": {"data": [...]}}`` shell;
      - parses a flat-list response shape;
      - parses the ``{"data": [...]}`` shell (no TradeStats wrapper);
      - drops non-4-digit HS codes (HS-2, HS-6, blank, garbage);
      - drops non-positive / non-numeric trade values;
      - non-200 status → skip that flow (no rows from that flow);
      - thrown exception in ``requests.get`` is swallowed → empty frame;
      - both flows empty → returns empty DataFrame;
      - flow code is ``"X"`` for exports, ``"M"`` for imports;
      - ``value_usd`` is the raw tradeValue × 1000 (WITS reports thousands);
      - ``net_weight_kg`` is the raw tradeValue × 500 (rough proxy);
      - ``country_iso2`` and ``source="wits"`` are stamped on every row;
      - ``date`` is the mid-year anchor ``{year}-06-01``.
  * ``fetch_all_ports`` —
      - default ``cache=None`` constructs a ``CacheManager`` (verified by
        redirecting its default cache_dir);
      - returns one DataFrame per port that has WITS data;
      - applies ``PORT_TRAFFIC_WEIGHTS`` to ``value_usd`` and
        ``net_weight_kg`` for multi-port countries (CHN, MYS, USA);
      - single-port countries get weight 1.0;
      - countries whose ISO3 isn't in ``_ISO3_TO_ISO2`` are skipped entirely
        (no ports for them are populated);
      - cache hit on the second call: same key → same value, no second
        WITS roundtrip;
      - cache key includes both ISO2 and year (distinct files);
      - falls back to ``_wb_merchandise_fallback`` when *every* WITS pull
        comes back empty.
  * ``_wb_merchandise_fallback`` —
      - returns synthetic per-port frames keyed off
        ``_category_shares_for_country``;
      - emits both flows (``X`` and ``M``) per category;
      - applies ``PORT_TRAFFIC_WEIGHTS`` the same way as the WITS path;
      - tolerates a country missing from the WB totals (uses 5e10 default);
      - stamps ``source="wb_synthetic"`` on every row.
  * ``get_top_products_for_port`` —
      - missing port → ``[]``;
      - empty DataFrame → ``[]``;
      - missing ``hs_code`` / ``value_usd`` columns → ``[]``;
      - returns ``top_n`` rows sorted by descending value;
      - groups by ``hs_code`` (sums duplicate codes);
      - filters by ``flow``;
      - falls back to the unfiltered frame when the flow filter is empty;
      - each row carries ``hs_code``, ``category`` (from
        ``ports.product_mapper.get_category``), and ``value_usd``.

Real findings worth pinning are noted inline.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest

import data.comtrade_feed as cf
from data.cache_manager import CacheManager
from ports.port_registry import PORTS, PORT_TRAFFIC_WEIGHTS


# ─── helpers ────────────────────────────────────────────────────────────────


def _make_response(payload: Any, status: int = 200) -> MagicMock:
    """Mimic a ``requests.Response`` returning ``payload`` from ``.json()``."""
    resp = MagicMock()
    resp.json.return_value = payload
    resp.status_code = status
    return resp


def _wits_record(*, product_code: str, value: float) -> dict:
    """A single WITS HS-4 trade record (TradeStats data array shape)."""
    return {"productCode": product_code, "tradeValue": value}


def _wits_payload(records: list[dict]) -> dict:
    """The canonical ``{"TradeStats": {"data": [...]}}`` envelope."""
    return {"TradeStats": {"datasource": "tradeStats", "data": records}}


# Strip the @st.cache_data wrapper for direct invocation in tests.
_fetch_all_ports_raw = cf.fetch_all_ports.__wrapped__
# Strip the tenacity @retry wrapper so failure paths run once, not 2x.
_fetch_wits_country_raw = cf._fetch_wits_country.__wrapped__


@pytest.fixture(autouse=True)
def _reset_cache_memo():
    """Clear the streamlit in-process memo between tests."""
    try:
        cf.fetch_all_ports.clear()
    except Exception:
        pass
    yield
    try:
        cf.fetch_all_ports.clear()
    except Exception:
        pass


@pytest.fixture
def disk_cache(tmp_path) -> CacheManager:
    return CacheManager(tmp_path / "cache")


# ─── module-level reference tables ──────────────────────────────────────────


def test_iso3_to_iso2_table_well_formed():
    """Every ISO3 key is exactly 3 chars, every ISO2 value is exactly 2."""
    for iso3, iso2 in cf._ISO3_TO_ISO2.items():
        assert len(iso3) == 3, f"{iso3!r} is not alpha-3"
        assert len(iso2) == 2, f"{iso2!r} is not alpha-2"
        assert iso3.isupper() and iso2.isupper()


def test_iso3_to_iso2_covers_every_port_country():
    """Otherwise some PORTS would be silently skipped by fetch_all_ports."""
    port_countries = {p.country_iso3 for p in PORTS}
    missing = port_countries - set(cf._ISO3_TO_ISO2)
    assert missing == set(), f"Ports for {missing} have no ISO3→ISO2 mapping"


def test_hs_category_map_all_codes_are_hs4():
    """The fetch loop hard-filters to ``len == 4``; pin that the table
    only contains HS-4 keys (otherwise entries would never match)."""
    for code in cf._HS_CATEGORY_MAP:
        assert len(code) == 4
        assert code.isdigit()


def test_hs_category_map_categories_are_known():
    """Every category mapped from an HS-4 must exist in _CATEGORY_SHARES,
    otherwise the synthetic fallback could fail to weight it."""
    for category in cf._HS_CATEGORY_MAP.values():
        assert category in cf._CATEGORY_SHARES


def test_category_shares_sums_to_one():
    """The global shares table should be a (near-)probability distribution."""
    assert sum(cf._CATEGORY_SHARES.values()) == pytest.approx(1.0)


def test_country_category_shares_use_known_categories():
    """Every per-country row uses only categories known to the global table.
    A typo (e.g. ``"electonics"``) would silently zero out that country's
    exports for the corresponding HS code, so pin it."""
    known = set(cf._CATEGORY_SHARES)
    for iso2, row in cf._COUNTRY_CATEGORY_SHARES.items():
        assert set(row) == known, f"{iso2} has mismatched categories: {set(row) ^ known}"


# ─── _category_shares_for_country ───────────────────────────────────────────


def test_category_shares_for_country_known_country_normalised():
    """A known country's shares come back summing to 1.0 (post-normalise)."""
    out = cf._category_shares_for_country("CN")
    assert sum(out.values()) == pytest.approx(1.0)
    # CN row leads with electronics — preserved through normalisation.
    assert max(out, key=out.get) == "electronics"


def test_category_shares_for_country_unknown_falls_back_to_global():
    """An ISO2 not in the per-country table → global ``_CATEGORY_SHARES``,
    also normalised to 1.0."""
    out = cf._category_shares_for_country("ZZ")
    assert sum(out.values()) == pytest.approx(1.0)
    assert set(out) == set(cf._CATEGORY_SHARES)


def test_category_shares_for_country_preserves_ratios():
    """Normalisation preserves the relative ordering of the input."""
    raw = cf._COUNTRY_CATEGORY_SHARES["KR"]
    out = cf._category_shares_for_country("KR")
    raw_order = sorted(raw, key=raw.get, reverse=True)
    out_order = sorted(out, key=out.get, reverse=True)
    assert raw_order == out_order


def test_category_shares_for_country_values_non_negative():
    """No category should come out negative after normalisation."""
    for iso2 in list(cf._COUNTRY_CATEGORY_SHARES) + ["ZZ"]:
        out = cf._category_shares_for_country(iso2)
        for cat, val in out.items():
            assert val >= 0.0, f"{iso2}/{cat} → {val}"


# ─── _fetch_wits_country: URL construction ──────────────────────────────────


def test_fetch_wits_country_hits_both_flow_urls(monkeypatch):
    """The function must request both exports and imports for the given
    iso2/year. Pin the exact URL shape for each."""
    captured: list[str] = []

    def fake_get(url, params=None, timeout=None):
        captured.append(url)
        return _make_response(_wits_payload([]))

    monkeypatch.setattr(cf.requests, "get", fake_get)
    _fetch_wits_country_raw("CN", "2024")

    assert captured == [
        f"{cf._WITS_BASE}/CN/2024/all/exports",
        f"{cf._WITS_BASE}/CN/2024/all/imports",
    ]


def test_fetch_wits_country_uses_json_format_param(monkeypatch):
    """Pin ``params={"format": "JSON"}`` and a 30s timeout."""
    captured: dict = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        captured["timeout"] = timeout
        return _make_response(_wits_payload([]))

    monkeypatch.setattr(cf.requests, "get", fake_get)
    _fetch_wits_country_raw("US", "2024")
    assert captured["params"] == {"format": "JSON"}
    assert captured["timeout"] == 30


# ─── _fetch_wits_country: response parsing ──────────────────────────────────


def test_fetch_wits_country_parses_tradestats_envelope(monkeypatch):
    """The ``{"TradeStats": {"data": [...]}}`` envelope is the canonical
    WITS response shape — pin parsing of it."""
    payload = _wits_payload([
        _wits_record(product_code="8471", value=1_000.0),  # electronics
        _wits_record(product_code="2902", value=500.0),    # chemicals
    ])
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _make_response(payload))

    df = _fetch_wits_country_raw("CN", "2024")
    # Two flows × 2 records = 4 rows.
    assert len(df) == 4
    assert set(df["hs_code"]) == {"8471", "2902"}
    assert set(df["flow"]) == {"X", "M"}
    assert (df["country_iso2"] == "CN").all()
    assert (df["source"] == "wits").all()


def test_fetch_wits_country_parses_bare_list_response(monkeypatch):
    """Some WITS endpoints / years return a bare JSON list of records.
    The parser must accept this shape too."""
    records = [
        _wits_record(product_code="8703", value=2_000.0),
    ]
    # Bare list response.
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _make_response(records))

    df = _fetch_wits_country_raw("DE", "2024")
    assert not df.empty
    assert (df["hs_code"] == "8703").all()


def test_fetch_wits_country_parses_plain_data_envelope(monkeypatch):
    """The fallback ``{"data": [...]}`` envelope (no TradeStats key) parses too."""
    payload = {"data": [_wits_record(product_code="1001", value=300.0)]}
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _make_response(payload))

    df = _fetch_wits_country_raw("US", "2024")
    assert not df.empty
    assert (df["hs_code"] == "1001").all()


def test_fetch_wits_country_drops_non_hs4_codes(monkeypatch):
    """Only 4-digit HS codes are kept; HS-2, HS-6, blank, garbage all drop."""
    payload = _wits_payload([
        _wits_record(product_code="84",     value=100.0),  # HS-2 → drop
        _wits_record(product_code="847130", value=100.0),  # HS-6 → drop
        _wits_record(product_code="",       value=100.0),  # blank → drop
        _wits_record(product_code="8471",   value=100.0),  # HS-4 → keep
    ])
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _make_response(payload))

    df = _fetch_wits_country_raw("CN", "2024")
    assert set(df["hs_code"]) == {"8471"}


def test_fetch_wits_country_accepts_cmdcode_alias(monkeypatch):
    """The parser tries ``productCode`` then ``cmdCode`` — pin both work."""
    payload = _wits_payload([
        {"cmdCode": "8471", "tradeValue": 100.0},
    ])
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _make_response(payload))
    df = _fetch_wits_country_raw("CN", "2024")
    assert (df["hs_code"] == "8471").all()


def test_fetch_wits_country_accepts_value_alias(monkeypatch):
    """``value`` is accepted alongside ``tradeValue``."""
    payload = _wits_payload([
        {"productCode": "8471", "value": 42.0},
    ])
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _make_response(payload))
    df = _fetch_wits_country_raw("CN", "2024")
    assert not df.empty
    assert df["value_usd"].iloc[0] == pytest.approx(42.0 * 1000)


def test_fetch_wits_country_drops_zero_and_negative_values(monkeypatch):
    """``value <= 0`` rows must be filtered (avoid noisy / sentinel rows)."""
    payload = _wits_payload([
        _wits_record(product_code="8471", value=0.0),
        _wits_record(product_code="8517", value=-5.0),
        _wits_record(product_code="8542", value=10.0),  # keep
    ])
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _make_response(payload))
    df = _fetch_wits_country_raw("CN", "2024")
    assert set(df["hs_code"]) == {"8542"}


def test_fetch_wits_country_drops_non_numeric_values(monkeypatch):
    """``value="N/A"`` must be skipped, not crash with a coercion exception."""
    payload = _wits_payload([
        {"productCode": "8471", "tradeValue": "N/A"},
        {"productCode": "8517", "tradeValue": 100.0},  # keep
    ])
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _make_response(payload))
    df = _fetch_wits_country_raw("CN", "2024")
    assert set(df["hs_code"]) == {"8517"}


def test_fetch_wits_country_skips_non_dict_records(monkeypatch):
    """A malformed record (string / None inside the list) is silently skipped."""
    payload = _wits_payload([
        "this is not a dict",
        None,
        _wits_record(product_code="8471", value=100.0),  # keep
    ])
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _make_response(payload))
    df = _fetch_wits_country_raw("CN", "2024")
    assert set(df["hs_code"]) == {"8471"}


# ─── _fetch_wits_country: error / empty paths ───────────────────────────────


def test_fetch_wits_country_skips_non_200(monkeypatch):
    """A 500 status on the WITS endpoint → that flow yields no rows. Both
    flows 500 → empty DataFrame (no exception)."""
    monkeypatch.setattr(
        cf.requests, "get",
        lambda *a, **k: _make_response({}, status=500),
    )
    df = _fetch_wits_country_raw("CN", "2024")
    assert df.empty


def test_fetch_wits_country_swallows_request_exceptions(monkeypatch):
    """A thrown exception in ``requests.get`` is caught — both flows skipped
    → empty DataFrame returned (NOT propagated to the retry decorator).

    Finding: this is consistent with the worldbank_feed contract, but it
    means the tenacity decorator on this function can only retry timeouts
    raised *after* this try/except (i.e. effectively never via this path)."""
    def boom(*a, **k):
        raise RuntimeError("DNS dead")

    monkeypatch.setattr(cf.requests, "get", boom)
    df = _fetch_wits_country_raw("CN", "2024")
    assert df.empty


def test_fetch_wits_country_value_scaling_and_proxies(monkeypatch):
    """value_usd = raw × 1000, net_weight_kg = raw × 500 (WITS thousands).
    Both flows must contribute their record."""
    payload = _wits_payload([
        _wits_record(product_code="8471", value=10.0),
    ])
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _make_response(payload))
    df = _fetch_wits_country_raw("CN", "2024")
    # 1 record × 2 flows = 2 rows.
    assert len(df) == 2
    assert (df["value_usd"] == 10_000.0).all()
    assert (df["net_weight_kg"] == 5_000.0).all()


def test_fetch_wits_country_flow_codes_are_X_and_M(monkeypatch):
    """Exports → ``"X"``, imports → ``"M"`` — the canonical UN flow codes."""
    payload = _wits_payload([_wits_record(product_code="8471", value=1.0)])
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _make_response(payload))
    df = _fetch_wits_country_raw("CN", "2024")
    assert sorted(df["flow"].tolist()) == ["M", "X"]


def test_fetch_wits_country_date_anchored_mid_year(monkeypatch):
    """All rows for a given year are timestamped to ``YYYY-06-01``."""
    payload = _wits_payload([_wits_record(product_code="8471", value=1.0)])
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _make_response(payload))
    df = _fetch_wits_country_raw("CN", "2023")
    assert (df["date"] == pd.Timestamp("2023-06-01")).all()


# ─── fetch_all_ports: orchestration ─────────────────────────────────────────


def _wits_factory_with_payload(country_to_records: dict[str, list[dict]]):
    """Return a fake ``requests.get`` that inspects the URL for the iso2
    segment and returns the matching country's records (or empty)."""
    def fake_get(url, params=None, timeout=None):
        # URL shape: {base}/{iso2}/{year}/all/{flow}
        segments = url.split("/")
        # The iso2 lives 4 segments before the flow.
        iso2 = segments[-4]
        records = country_to_records.get(iso2, [])
        return _make_response(_wits_payload(records))
    return fake_get


def test_fetch_all_ports_returns_per_port_dict(monkeypatch, disk_cache):
    """With WITS returning data for every country, fetch_all_ports must
    produce one DataFrame per port that maps to a covered country."""
    # Hand every country one HS-4 trade record so all ports get rows.
    fake_get = _wits_factory_with_payload({
        iso2: [_wits_record(product_code="8471", value=1_000.0)]
        for iso2 in cf._ISO3_TO_ISO2.values()
    })
    monkeypatch.setattr(cf.requests, "get", fake_get)

    out = _fetch_all_ports_raw(
        lookback_months=3, cache=disk_cache, ttl_hours=24.0,
    )
    expected_ports = {p.locode for p in PORTS if p.country_iso3 in cf._ISO3_TO_ISO2}
    assert set(out.keys()) == expected_ports
    for locode, df in out.items():
        assert isinstance(df, pd.DataFrame)
        assert not df.empty


def test_fetch_all_ports_stamps_port_locode(monkeypatch, disk_cache):
    """Every row in the per-port frame carries its target port_locode."""
    fake_get = _wits_factory_with_payload({
        "CN": [_wits_record(product_code="8471", value=1_000.0)],
    })
    monkeypatch.setattr(cf.requests, "get", fake_get)

    out = _fetch_all_ports_raw(
        lookback_months=3, cache=disk_cache, ttl_hours=24.0,
    )
    assert "CNSHA" in out
    assert (out["CNSHA"]["port_locode"] == "CNSHA").all()


def test_fetch_all_ports_applies_port_traffic_weight(monkeypatch, disk_cache):
    """A multi-port country (CHN: Shanghai 0.27, Ningbo 0.22) must scale
    each port's value_usd and net_weight_kg by its weight."""
    raw_value = 1_000.0   # WITS thousands → value_usd = 1_000_000
    fake_get = _wits_factory_with_payload({
        "CN": [_wits_record(product_code="8471", value=raw_value)],
    })
    monkeypatch.setattr(cf.requests, "get", fake_get)

    out = _fetch_all_ports_raw(
        lookback_months=3, cache=disk_cache, ttl_hours=24.0,
    )
    # Each year produces value_usd = 1_000_000; two years are pulled.
    # Compare Shanghai/Ningbo ratios — robust against the year count.
    sha_total = out["CNSHA"]["value_usd"].sum()
    ngb_total = out["CNNBO"]["value_usd"].sum()
    expected_ratio = PORT_TRAFFIC_WEIGHTS["CHN"]["CNSHA"] / PORT_TRAFFIC_WEIGHTS["CHN"]["CNNBO"]
    assert sha_total / ngb_total == pytest.approx(expected_ratio)


def test_fetch_all_ports_single_port_country_weight_one(monkeypatch, disk_cache):
    """A country listed in PORT_TRAFFIC_WEIGHTS with weight 1.0 (e.g.
    Singapore) is unscaled — value_usd matches the raw WITS thousands."""
    fake_get = _wits_factory_with_payload({
        "SG": [_wits_record(product_code="8471", value=100.0)],
    })
    monkeypatch.setattr(cf.requests, "get", fake_get)

    out = _fetch_all_ports_raw(
        lookback_months=3, cache=disk_cache, ttl_hours=24.0,
    )
    assert "SGSIN" in out
    # Per record: 100 * 1000 = 100_000. Both flows (X+M) and 2 years → 4 rows.
    assert (out["SGSIN"]["value_usd"] == 100_000.0).all()


def test_fetch_all_ports_caches_on_second_call(monkeypatch, disk_cache):
    """The second invocation with the same cache should not re-hit WITS.

    We count requests.get calls; first invocation pays for every
    (iso2, year, flow), second hits the parquet cache instead."""
    calls: list[int] = [0]

    def counting_get(url, params=None, timeout=None):
        calls[0] += 1
        return _make_response(_wits_payload([
            _wits_record(product_code="8471", value=1.0),
        ]))

    monkeypatch.setattr(cf.requests, "get", counting_get)
    _fetch_all_ports_raw(lookback_months=3, cache=disk_cache, ttl_hours=24.0)
    n_first = calls[0]
    _fetch_all_ports_raw(lookback_months=3, cache=disk_cache, ttl_hours=24.0)
    assert calls[0] == n_first  # no extra WITS calls


def test_fetch_all_ports_distinct_years_use_distinct_cache_entries(
    monkeypatch, disk_cache, tmp_path,
):
    """Cache key is f"wits_{iso2}_{year}" — two years per country → two
    parquet files per country."""
    fake_get = _wits_factory_with_payload({
        "SG": [_wits_record(product_code="8471", value=1.0)],
    })
    monkeypatch.setattr(cf.requests, "get", fake_get)
    _fetch_all_ports_raw(lookback_months=3, cache=disk_cache, ttl_hours=24.0)

    comtrade_dir = disk_cache.cache_dir / "comtrade"
    sg_files = list(comtrade_dir.glob("wits_sg_*.parquet"))
    # Two years requested per country → two files.
    assert len(sg_files) == 2


def test_fetch_all_ports_default_cache_constructs_cachemanager(
    monkeypatch, tmp_path,
):
    """``cache=None`` must construct a ``CacheManager()``. Redirect its
    default cache_dir at a tmp path so the repo ``cache/`` is untouched."""
    seen = {"made": 0}
    real_init = CacheManager.__init__

    def spy_init(self, cache_dir="cache"):
        seen["made"] += 1
        real_init(self, tmp_path / "cache")

    monkeypatch.setattr(CacheManager, "__init__", spy_init)
    monkeypatch.setattr(
        cf.requests, "get",
        lambda *a, **k: _make_response(_wits_payload([])),
    )

    _fetch_all_ports_raw(lookback_months=3, cache=None, ttl_hours=1.0)
    assert seen["made"] >= 1


def test_fetch_all_ports_falls_back_to_wb_merchandise(
    monkeypatch, disk_cache,
):
    """When every WITS pull is empty, the code switches to the WB
    merchandise indicator endpoint and synthesises per-country sector
    mixes. The result dict is non-empty even though WITS gave nothing."""
    def fake_get(url, params=None, timeout=None):
        if cf._WITS_BASE in url:
            return _make_response(_wits_payload([]))   # WITS empty
        # WB merchandise indicator response: [meta, [records...]]
        # Hand back one record per requested ISO2.
        # The url shape is /v2/country/{joined}/indicator/{id}
        records = []
        for iso2 in cf._ISO3_TO_ISO2.values():
            records.append({
                "country": {"id": iso2},
                "value": 1.0e10,
            })
        return _make_response([{"page": 1}, records])

    monkeypatch.setattr(cf.requests, "get", fake_get)
    out = _fetch_all_ports_raw(
        lookback_months=3, cache=disk_cache, ttl_hours=24.0,
    )
    expected_ports = {p.locode for p in PORTS if p.country_iso3 in cf._ISO3_TO_ISO2}
    assert set(out.keys()) == expected_ports
    # Synthetic source must be stamped through.
    for locode, df in out.items():
        assert (df["source"] == "wb_synthetic").all()


# ─── _wb_merchandise_fallback ───────────────────────────────────────────────


def test_wb_fallback_emits_both_flows(monkeypatch, disk_cache):
    """Each category produces both an X (export) and an M (import) row."""
    def fake_get(url, params=None, timeout=None):
        records = [{"country": {"id": "SG"}, "value": 1.0e10}]
        return _make_response([{"page": 1}, records])

    monkeypatch.setattr(cf.requests, "get", fake_get)
    out = cf._wb_merchandise_fallback(disk_cache, ttl_hours=24.0)
    assert "SGSIN" in out
    df = out["SGSIN"]
    assert set(df["flow"]) == {"X", "M"}
    # 7 categories × 2 flows = 14 rows per port.
    assert len(df) == 7 * 2


def test_wb_fallback_applies_port_traffic_weight(monkeypatch, disk_cache):
    """Multi-port countries get their per-port weight applied."""
    def fake_get(url, params=None, timeout=None):
        return _make_response([{"page": 1}, [
            {"country": {"id": "CN"}, "value": 1.0e10},
        ]])

    monkeypatch.setattr(cf.requests, "get", fake_get)
    out = cf._wb_merchandise_fallback(disk_cache, ttl_hours=24.0)
    assert "CNSHA" in out and "CNNBO" in out
    sha_total = out["CNSHA"]["value_usd"].sum()
    ngb_total = out["CNNBO"]["value_usd"].sum()
    ratio = PORT_TRAFFIC_WEIGHTS["CHN"]["CNSHA"] / PORT_TRAFFIC_WEIGHTS["CHN"]["CNNBO"]
    assert sha_total / ngb_total == pytest.approx(ratio)


def test_wb_fallback_uses_default_total_when_country_missing(
    monkeypatch, disk_cache,
):
    """If the WB indicator response has no record for a country, the code
    falls back to 5e10 for both export and import totals. The resulting
    frame must still be present (no KeyError)."""
    # Return no records → every country falls back to 5e10.
    monkeypatch.setattr(
        cf.requests, "get",
        lambda *a, **k: _make_response([{"page": 1}, []]),
    )
    out = cf._wb_merchandise_fallback(disk_cache, ttl_hours=24.0)
    expected_ports = {p.locode for p in PORTS if p.country_iso3 in cf._ISO3_TO_ISO2}
    assert set(out.keys()) == expected_ports
    # Per-port total = 5e10 × weight × sum(shares=1) for X plus same for M.
    sg = out["SGSIN"]
    assert sg["value_usd"].sum() == pytest.approx(5e10 * 2)  # X + M sums


def test_wb_fallback_swallows_request_exception(monkeypatch, disk_cache):
    """A thrown ``requests.get`` exception must NOT propagate — totals
    default to 5e10 and the fallback still produces port frames."""
    monkeypatch.setattr(
        cf.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    out = cf._wb_merchandise_fallback(disk_cache, ttl_hours=24.0)
    assert out  # non-empty


def test_wb_fallback_source_stamp(monkeypatch, disk_cache):
    """Every row carries ``source="wb_synthetic"`` so downstream code can
    distinguish real WITS data from the synthetic fallback."""
    monkeypatch.setattr(
        cf.requests, "get",
        lambda *a, **k: _make_response([{"page": 1}, [
            {"country": {"id": "SG"}, "value": 1.0e10},
        ]]),
    )
    out = cf._wb_merchandise_fallback(disk_cache, ttl_hours=24.0)
    for locode, df in out.items():
        assert (df["source"] == "wb_synthetic").all()


# ─── get_top_products_for_port ──────────────────────────────────────────────


def _trade_frame(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    """rows: list of (hs_code, flow, value_usd)."""
    return pd.DataFrame([
        {"hs_code": h, "flow": f, "value_usd": v} for h, f, v in rows
    ])


def test_get_top_products_returns_top_n_by_value():
    """Sorted by descending value_usd; capped at ``top_n``."""
    df = _trade_frame([
        ("8471", "M", 100.0),
        ("8703", "M", 300.0),
        ("2902", "M", 200.0),
        ("1001", "M", 50.0),
    ])
    out = cf.get_top_products_for_port("CNSHA", {"CNSHA": df}, top_n=2, flow="M")
    assert len(out) == 2
    assert [row["hs_code"] for row in out] == ["8703", "2902"]
    assert out[0]["value_usd"] == 300.0


def test_get_top_products_groups_duplicates_by_hs_code():
    """Two rows with the same hs_code are summed before ranking."""
    df = _trade_frame([
        ("8471", "M", 100.0),
        ("8471", "M", 200.0),  # same hs_code → group/sum to 300
        ("8703", "M", 250.0),
    ])
    out = cf.get_top_products_for_port("CNSHA", {"CNSHA": df}, top_n=2, flow="M")
    top = out[0]
    assert top["hs_code"] == "8471"
    assert top["value_usd"] == 300.0


def test_get_top_products_filters_by_flow():
    """Only the requested flow contributes. Default flow is ``"M"``."""
    df = _trade_frame([
        ("8471", "X", 1_000.0),  # ignored when flow="M"
        ("8703", "M", 250.0),
        ("2902", "M", 100.0),
    ])
    out = cf.get_top_products_for_port("CNSHA", {"CNSHA": df}, top_n=3, flow="M")
    assert {row["hs_code"] for row in out} == {"8703", "2902"}


def test_get_top_products_returns_empty_for_missing_port():
    """Port absent from the dict → ``[]``."""
    assert cf.get_top_products_for_port("ZZZZZ", {}, top_n=3) == []


def test_get_top_products_returns_empty_for_empty_dataframe():
    """Empty DataFrame → ``[]``."""
    assert cf.get_top_products_for_port(
        "CNSHA", {"CNSHA": pd.DataFrame()}, top_n=3,
    ) == []


def test_get_top_products_returns_empty_when_columns_missing():
    """Missing ``hs_code`` or ``value_usd`` columns → ``[]``.

    Finding: ``flow`` is treated as optional via the ``if "flow" in
    df.columns`` guard, but the hs_code/value_usd columns are required."""
    df = pd.DataFrame({"foo": [1, 2, 3]})
    assert cf.get_top_products_for_port("CNSHA", {"CNSHA": df}, top_n=3) == []


def test_get_top_products_falls_back_to_unfiltered_when_flow_empty():
    """If the requested flow has no rows, the function falls back to the
    unfiltered frame rather than returning ``[]``."""
    df = _trade_frame([
        ("8471", "X", 100.0),
        ("8703", "X", 200.0),
    ])
    # No "M" rows at all — fall back to unfiltered → returns X rows.
    out = cf.get_top_products_for_port("CNSHA", {"CNSHA": df}, top_n=2, flow="M")
    assert len(out) == 2
    assert {row["hs_code"] for row in out} == {"8471", "8703"}


def test_get_top_products_attaches_category_label():
    """Each result row has a ``category`` field from
    ``ports.product_mapper.get_category``."""
    from ports.product_mapper import get_category

    df = _trade_frame([
        ("8471", "M", 100.0),  # electronics in product_mapper
    ])
    out = cf.get_top_products_for_port("CNSHA", {"CNSHA": df}, top_n=1, flow="M")
    assert out[0]["category"] == get_category("8471")


def test_get_top_products_handles_frame_without_flow_column():
    """If ``flow`` isn't in the frame, every row is considered (no filter)."""
    df = pd.DataFrame([
        {"hs_code": "8471", "value_usd": 100.0},
        {"hs_code": "8703", "value_usd": 200.0},
    ])
    out = cf.get_top_products_for_port("CNSHA", {"CNSHA": df}, top_n=2, flow="M")
    assert len(out) == 2
    assert {row["hs_code"] for row in out} == {"8471", "8703"}
