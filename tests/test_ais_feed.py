"""Tests for ``data.ais_feed``.

This module fetches vessel-count data for each tracked port (IMF PortWatch
first, with a smart synthetic fallback) and exposes helpers for "vessel count
for one port" and a "congestion index" derived from a z-score across all
ports. Every test in this file mocks the network so no real HTTP request is
ever made.

The ``fetch_vessel_counts`` symbol is wrapped by ``st.cache_data``; tests use
its ``.__wrapped__`` form to bypass Streamlit's in-process cache. The
``_fetch_portwatch_all`` symbol is wrapped by ``@retry`` from tenacity;
``.__wrapped__`` strips the retry shell so failure-path tests don't pay
multi-second backoff. Note: a separate module ``data.aisstream_feed`` exists
in the codebase — this file deliberately tests ``data.ais_feed`` only.

Covers:
  - Module-level configuration tables
      * _PORT_VESSEL_BASELINES has a positive int for every known port LOCODE
      * _SEASONAL has all 12 months and every multiplier is a positive float
      * _PORT_SEASONAL_SENSITIVITY has positive floats only

  - _interp_seasonal
      * Integer-month input returns exactly _SEASONAL[m]
      * Fractional month is the convex blend of the two bracketing months
      * Month 12.5 wraps cleanly to January (no KeyError)
      * Continuous across month boundary (m -> m+1) within tolerance

  - _synthetic_congestion (network not needed — fully deterministic)
      * Returns a single-row DataFrame with the AIS_COLS schema after
        normalize_ais_df has run
      * port_locode matches the requested locode
      * source column is "aishub" (normalize_ais_df overwrites it)
      * vessel_count >= 1 (the max-clamp must hold)
      * Known port → vessel_count tracks the baseline within tolerance
        (seasonal * smooth daily variation only)
      * Unknown port → falls back to the default baseline of 40 (within tol)
      * Two calls for the same port on the same day match (deterministic)

  - _fetch_portwatch_all (network mocked; @retry stripped via __wrapped__)
      * Every endpoint returns non-200 → returns an empty DataFrame
      * Every endpoint raises an exception → returns an empty DataFrame
      * Endpoint returns JSON list with valid records → returns a normalized
        DataFrame containing only LOCODEs that are in PORTS_BY_LOCODE
      * Unknown LOCODEs in the response are filtered out
      * GeoJSON-shaped records (with "geometry" + "properties") parse
        correctly using the inner properties dict
      * Records missing port_id or vessel_count are skipped
      * Records with vessel_count == 0 are skipped (per "not vessel_count"
        truthiness check in the module)

  - fetch_vessel_counts (orchestrator, accessed via __wrapped__)
      * Returns one entry per port in PORTS even when PortWatch is empty
      * When PortWatch returns rows for some ports, those ports get the live
        data and the rest get synthetic fallback
      * When PortWatch fails / returns empty, every port gets synthetic data
      * A None cache argument constructs a default CacheManager and still
        produces a full result dict

  - get_vessel_count
      * Missing port in ais_data → falls back to baseline
      * Empty DataFrame for a port → falls back to baseline
      * Unknown port (not in baselines) → falls back to 40
      * "vessel_type" filter selects matching rows; non-matching → baseline
        fallback
      * Returns int(last vessel_count) when a matching row exists

  - compute_congestion_index
      * Returns 0.5 when ais_data is empty (no counts)
      * Returns 0.5 when std == 0 (every port has the same count)
      * Output is bounded to [0, 1] (sigmoid contract)
      * Current port above the mean → > 0.5
      * Current port below the mean → < 0.5
      * baseline_counts argument is used in place of ais_data totals
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from data import ais_feed
from data.cache_manager import CacheManager
from data.normalizer import AIS_COLS
from ports.port_registry import PORTS, PORTS_BY_LOCODE


# ─── Wrapper strippers ──────────────────────────────────────────────────────

# Bypass Streamlit's @st.cache_data so each test gets a fresh invocation
# and pytest fixtures (tmp_path caches, monkeypatched modules) take effect.
_fetch_vessel_counts_raw = ais_feed.fetch_vessel_counts.__wrapped__  # type: ignore[attr-defined]

# Bypass tenacity's @retry so failure-mode tests don't pay backoff time.
_fetch_portwatch_raw = ais_feed._fetch_portwatch_all.__wrapped__  # type: ignore[attr-defined]


# ─── Helpers ────────────────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal stand-in for ``requests.Response`` for the ais_feed code path."""

    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _patch_requests(monkeypatch, responder):
    """Patch ``ais_feed.requests.get`` with ``responder(url, **kw)``.

    Returns a list of captured URLs so tests can assert call shape.
    """
    captured: list[str] = []

    def _fake_get(url, *args, **kwargs):
        captured.append(url)
        return responder(url, **kwargs)

    class _FakeRequests:
        get = staticmethod(_fake_get)

    monkeypatch.setattr(ais_feed, "requests", _FakeRequests, raising=True)
    return captured


# ─── Configuration tables ───────────────────────────────────────────────────


def test_port_vessel_baselines_are_positive_ints():
    assert ais_feed._PORT_VESSEL_BASELINES, "baselines must not be empty"
    for locode, count in ais_feed._PORT_VESSEL_BASELINES.items():
        assert isinstance(locode, str) and len(locode) == 5
        assert isinstance(count, int) and count > 0


def test_seasonal_covers_all_12_months_with_positive_floats():
    assert set(ais_feed._SEASONAL) == set(range(1, 13))
    for month, mult in ais_feed._SEASONAL.items():
        assert isinstance(mult, float)
        assert mult > 0


def test_port_seasonal_sensitivity_positive_floats():
    for locode, sens in ais_feed._PORT_SEASONAL_SENSITIVITY.items():
        assert isinstance(locode, str) and len(locode) == 5
        assert isinstance(sens, float)
        assert sens > 0


# ─── _interp_seasonal ──────────────────────────────────────────────────────


def test_interp_seasonal_at_integer_month_matches_table():
    for m in range(1, 13):
        assert ais_feed._interp_seasonal(float(m)) == pytest.approx(
            ais_feed._SEASONAL[m]
        )


def test_interp_seasonal_at_midpoint_is_average_of_adjacent_months():
    # halfway between month 7 and month 8 should be the average of the two.
    halfway = ais_feed._interp_seasonal(7.5)
    expected = (ais_feed._SEASONAL[7] + ais_feed._SEASONAL[8]) / 2.0
    assert halfway == pytest.approx(expected)


def test_interp_seasonal_wraps_at_year_end():
    # 12.5 = halfway between December and January; must not raise KeyError.
    val = ais_feed._interp_seasonal(12.5)
    expected = (ais_feed._SEASONAL[12] + ais_feed._SEASONAL[1]) / 2.0
    assert val == pytest.approx(expected)


def test_interp_seasonal_continuous_across_month_boundary():
    """Approaching month m+1 from below must match m+1 in the limit."""
    just_below = ais_feed._interp_seasonal(5.0 + 1e-9)
    just_above = ais_feed._interp_seasonal(5.0 - 1e-9)
    # Both very close to _SEASONAL[5].
    assert just_below == pytest.approx(ais_feed._SEASONAL[5], abs=1e-3)
    assert just_above == pytest.approx(ais_feed._SEASONAL[5], abs=1e-3)


# ─── _synthetic_congestion ─────────────────────────────────────────────────


def test_synthetic_congestion_known_port_schema_and_value():
    df = ais_feed._synthetic_congestion("CNSHA")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    # normalize_ais_df enforces AIS_COLS ordering.
    assert list(df.columns) == AIS_COLS
    assert df["port_locode"].iloc[0] == "CNSHA"
    # normalize_ais_df overwrites source with "aishub".
    assert df["source"].iloc[0] == "aishub"
    # Baseline 180 * seasonal (~0.75..1.25) * variation (~0.92..1.08).
    count = int(df["vessel_count"].iloc[0])
    assert count >= 1
    baseline = ais_feed._PORT_VESSEL_BASELINES["CNSHA"]
    assert 0.55 * baseline <= count <= 1.50 * baseline


def test_synthetic_congestion_clamps_to_at_least_one():
    df = ais_feed._synthetic_congestion("CNSHA")
    assert int(df["vessel_count"].iloc[0]) >= 1


def test_synthetic_congestion_unknown_port_uses_default_40():
    df = ais_feed._synthetic_congestion("ZZZZZ")
    assert df["port_locode"].iloc[0] == "ZZZZZ"
    count = int(df["vessel_count"].iloc[0])
    # default baseline = 40; seasonal sensitivity falls back to 0.85.
    assert 1 <= count <= 80


def test_synthetic_congestion_deterministic_same_day():
    """Same port called twice on the same day should yield identical counts.

    The function uses datetime.now() but only at day granularity (.timetuple
    .tm_yday and .month / .day), plus stable_hash(port_locode) for phase. So
    consecutive calls within the same calendar day must match.
    """
    a = int(ais_feed._synthetic_congestion("USLAX")["vessel_count"].iloc[0])
    b = int(ais_feed._synthetic_congestion("USLAX")["vessel_count"].iloc[0])
    assert a == b


# ─── _fetch_portwatch_all: failure modes ───────────────────────────────────


def test_portwatch_all_endpoints_non_200_returns_empty(monkeypatch):
    def _responder(url, **kwargs):
        return _FakeResponse(status_code=500, payload={})

    urls = _patch_requests(monkeypatch, _responder)
    df = _fetch_portwatch_raw()
    assert isinstance(df, pd.DataFrame)
    assert df.empty
    # Every endpoint was tried.
    assert len(urls) >= 1


def test_portwatch_all_endpoints_raise_returns_empty(monkeypatch):
    def _responder(url, **kwargs):
        raise RuntimeError("network is down")

    _patch_requests(monkeypatch, _responder)
    df = _fetch_portwatch_raw()
    assert isinstance(df, pd.DataFrame)
    assert df.empty


# ─── _fetch_portwatch_all: happy path / parsing ────────────────────────────


def test_portwatch_parses_flat_list_payload(monkeypatch):
    """JSON list of {portid, vessel_count} → normalized DataFrame."""
    payload = [
        {"portid": "CNSHA", "vessel_count": 200},
        {"portid": "SGSIN", "vessel_count": 100},
        # Lowercase locode must still match because the module upper()s it.
        {"portid": "uslax", "vessel_count": 70},
    ]

    def _responder(url, **kwargs):
        return _FakeResponse(200, payload)

    _patch_requests(monkeypatch, _responder)
    df = _fetch_portwatch_raw()

    assert not df.empty
    assert set(df.columns) == set(AIS_COLS)
    # All locodes should be matched and uppercased.
    locodes = set(df["port_locode"])
    assert {"CNSHA", "SGSIN", "USLAX"}.issubset(locodes)


def test_portwatch_filters_unknown_locodes(monkeypatch):
    """Records whose LOCODE isn't in PORTS_BY_LOCODE are dropped."""
    payload = [
        {"portid": "CNSHA", "vessel_count": 200},   # valid
        {"portid": "XXXXX", "vessel_count": 999},   # bogus
        {"portid": "FAKE9", "vessel_count": 50},    # bogus
    ]

    def _responder(url, **kwargs):
        return _FakeResponse(200, payload)

    _patch_requests(monkeypatch, _responder)
    df = _fetch_portwatch_raw()

    assert not df.empty
    seen = set(df["port_locode"])
    assert "CNSHA" in seen
    assert "XXXXX" not in seen
    assert "FAKE9" not in seen


def test_portwatch_parses_geojson_feature_payload(monkeypatch):
    """records with "geometry" use rec["properties"] as the source dict."""
    payload = {
        "data": [
            {
                "geometry": {"type": "Point", "coordinates": [121.47, 31.23]},
                "properties": {"portid": "CNSHA", "portcalls": 150},
            },
            {
                "geometry": {"type": "Point", "coordinates": [103.85, 1.29]},
                "properties": {"port_id": "SGSIN", "calls": 90},
            },
        ]
    }

    def _responder(url, **kwargs):
        return _FakeResponse(200, payload)

    _patch_requests(monkeypatch, _responder)
    df = _fetch_portwatch_raw()

    assert not df.empty
    assert "CNSHA" in set(df["port_locode"])
    assert "SGSIN" in set(df["port_locode"])


def test_portwatch_skips_records_missing_required_fields(monkeypatch):
    """Records lacking port_id OR vessel_count are filtered out."""
    payload = [
        {"portid": "CNSHA", "vessel_count": 200},   # valid
        {"portid": "SGSIN"},                        # missing count
        {"vessel_count": 99},                       # missing portid
        {"portid": "USLAX", "vessel_count": 0},     # 0 count treated as missing
    ]

    def _responder(url, **kwargs):
        return _FakeResponse(200, payload)

    _patch_requests(monkeypatch, _responder)
    df = _fetch_portwatch_raw()

    assert not df.empty
    seen = set(df["port_locode"])
    assert seen == {"CNSHA"}


# ─── fetch_vessel_counts (orchestrator) ────────────────────────────────────


def test_fetch_vessel_counts_synthetic_when_portwatch_empty(monkeypatch, tmp_path):
    """If PortWatch returns nothing, every port gets a synthetic frame."""

    def _responder(url, **kwargs):
        return _FakeResponse(404, {})

    _patch_requests(monkeypatch, _responder)
    cache = CacheManager(cache_dir=tmp_path / "cache")

    out = _fetch_vessel_counts_raw(cache=cache)

    # One entry per port in PORTS.
    assert set(out.keys()) == {p.locode for p in PORTS}
    for locode, df in out.items():
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        # synthetic frames go through normalize_ais_df which sets source.
        assert df["source"].iloc[0] == "aishub"


def test_fetch_vessel_counts_mixed_live_and_synthetic(monkeypatch, tmp_path):
    """PortWatch returns CNSHA and SGSIN; everyone else gets synthetic."""
    payload = [
        {"portid": "CNSHA", "vessel_count": 250},
        {"portid": "SGSIN", "vessel_count": 110},
    ]

    def _responder(url, **kwargs):
        return _FakeResponse(200, payload)

    _patch_requests(monkeypatch, _responder)
    cache = CacheManager(cache_dir=tmp_path / "cache")

    out = _fetch_vessel_counts_raw(cache=cache)

    assert set(out.keys()) == {p.locode for p in PORTS}
    # The two ports PortWatch covered come from the live frame.
    assert int(out["CNSHA"]["vessel_count"].iloc[-1]) == 250
    assert int(out["SGSIN"]["vessel_count"].iloc[-1]) == 110
    # A port PortWatch didn't cover still has a (synthetic) frame.
    assert not out["USLAX"].empty


def test_fetch_vessel_counts_default_cache_argument(monkeypatch, tmp_path):
    """A None cache argument must still produce a complete result dict.

    We point CacheManager at tmp_path so the default-constructed instance
    doesn't write into the repo's cache/ directory.
    """

    def _responder(url, **kwargs):
        return _FakeResponse(500, {})

    _patch_requests(monkeypatch, _responder)

    real_cm = CacheManager

    def _scoped_cm(*args, **kwargs):
        # Force any default cache_dir to tmp_path.
        kwargs.setdefault("cache_dir", tmp_path / "cache")
        return real_cm(*args, **kwargs)

    monkeypatch.setattr(ais_feed, "CacheManager", _scoped_cm)

    out = _fetch_vessel_counts_raw(cache=None)
    assert set(out.keys()) == {p.locode for p in PORTS}


# ─── get_vessel_count ──────────────────────────────────────────────────────


def test_get_vessel_count_missing_port_returns_baseline():
    out = ais_feed.get_vessel_count("CNSHA", {})
    assert out == ais_feed._PORT_VESSEL_BASELINES["CNSHA"]


def test_get_vessel_count_unknown_port_returns_40():
    out = ais_feed.get_vessel_count("ZZZZZ", {})
    assert out == 40


def test_get_vessel_count_empty_df_returns_baseline():
    out = ais_feed.get_vessel_count("USLAX", {"USLAX": pd.DataFrame()})
    assert out == ais_feed._PORT_VESSEL_BASELINES["USLAX"]


def test_get_vessel_count_returns_last_row_value():
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
        "port_locode": ["CNSHA", "CNSHA"],
        "vessel_count": [180, 220],
        "vessel_type": ["cargo", "cargo"],
        "source": ["test", "test"],
    })
    assert ais_feed.get_vessel_count("CNSHA", {"CNSHA": df}) == 220


def test_get_vessel_count_vessel_type_filter_misses_returns_baseline():
    """If filtering on vessel_type leaves no rows, fall back to baseline."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01"]),
        "port_locode": ["CNSHA"],
        "vessel_count": [180],
        "vessel_type": ["tanker"],   # not cargo
        "source": ["test"],
    })
    assert ais_feed.get_vessel_count("CNSHA", {"CNSHA": df}, "cargo") == \
        ais_feed._PORT_VESSEL_BASELINES["CNSHA"]


# ─── compute_congestion_index ──────────────────────────────────────────────


def _ais_df(locode: str, count: int) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01"]),
        "port_locode": [locode],
        "vessel_count": [count],
        "vessel_type": ["cargo"],
        "source": ["test"],
    })


def test_congestion_index_empty_data_returns_neutral():
    """Empty ais_data → no counts → returns the neutral 0.5."""
    assert ais_feed.compute_congestion_index("CNSHA", {}) == 0.5


def test_congestion_index_all_zero_counts_returns_neutral():
    """If every port has zero ships, return neutral 0.5."""
    data = {
        "CNSHA": _ais_df("CNSHA", 0),
        "SGSIN": _ais_df("SGSIN", 0),
    }
    assert ais_feed.compute_congestion_index("CNSHA", data) == 0.5


def test_congestion_index_zero_std_returns_neutral():
    """Same count across all ports → std==0 → neutral 0.5."""
    data = {
        "CNSHA": _ais_df("CNSHA", 100),
        "SGSIN": _ais_df("SGSIN", 100),
        "USLAX": _ais_df("USLAX", 100),
    }
    assert ais_feed.compute_congestion_index("CNSHA", data) == 0.5


def test_congestion_index_bounded_in_unit_interval():
    """sigmoid contract: every output is in [0, 1]."""
    data = {
        "CNSHA": _ais_df("CNSHA", 500),
        "SGSIN": _ais_df("SGSIN",   5),
        "USLAX": _ais_df("USLAX",  50),
    }
    for locode in data:
        val = ais_feed.compute_congestion_index(locode, data)
        assert 0.0 <= val <= 1.0


def test_congestion_index_above_mean_is_above_half():
    """Target port well above mean → sigmoid output > 0.5."""
    data = {
        "CNSHA": _ais_df("CNSHA", 300),
        "SGSIN": _ais_df("SGSIN",  50),
        "USLAX": _ais_df("USLAX",  50),
    }
    assert ais_feed.compute_congestion_index("CNSHA", data) > 0.5


def test_congestion_index_below_mean_is_below_half():
    """Target port well below mean → sigmoid output < 0.5."""
    data = {
        "CNSHA": _ais_df("CNSHA", 300),
        "SGSIN": _ais_df("SGSIN", 300),
        "USLAX": _ais_df("USLAX",  10),
    }
    assert ais_feed.compute_congestion_index("USLAX", data) < 0.5


def test_congestion_index_uses_baseline_counts_argument():
    """When baseline_counts is supplied, the stats are computed from it,
    not from ais_data."""
    data = {"CNSHA": _ais_df("CNSHA", 300)}
    # Pretend the rest of the world averages 50 ships.
    baselines = {"CNSHA": 50.0, "SGSIN": 50.0, "USLAX": 50.0, "NLRTM": 50.0}
    val = ais_feed.compute_congestion_index("CNSHA", data,
                                             baseline_counts=baselines)
    # CNSHA (300) is well above the baseline mean (50, std 0) → but std==0
    # so the function returns neutral 0.5.
    assert val == 0.5


def test_congestion_index_baseline_counts_with_variance_pushes_above_half():
    """A non-degenerate baseline distribution still resolves above 0.5 when
    the target is the high one."""
    data = {"CNSHA": _ais_df("CNSHA", 300)}
    baselines = {"CNSHA": 50.0, "SGSIN": 60.0, "USLAX": 40.0}
    val = ais_feed.compute_congestion_index("CNSHA", data,
                                             baseline_counts=baselines)
    assert val > 0.5


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
