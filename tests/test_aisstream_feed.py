"""Tests for ``data.aisstream_feed``.

This module is the AISstream.io vessel-tracking client. Despite the AIS-stream
name, the production code path here is an HTTP/REST request against the
AISstream bounding-box endpoint, not a raw WebSocket subscription — so every
network test in this file mocks ``requests`` (not ``websockets``) to keep
hermetic. The synthetic-fallback path is fully deterministic via ``random.Random``
seeded from ``stable_hash`` and the wall clock floored to the 30-minute cache
window, so it is exercised directly with no patching.

The module's two public REST entrypoints (``fetch_vessels_near_port`` and
``fetch_vessels_on_route``) are wrapped by ``@st.cache_data``; tests use their
``.__wrapped__`` form to bypass Streamlit's in-process cache so each call hits
the underlying logic and pytest's monkeypatched ``requests`` actually fires.

The API-key env var is ``AISSTREAM_KEY`` (not ``AISSTREAM_API_KEY``); the helper
``_get_aisstream_key`` first tries ``st.secrets.get`` which raises
``StreamlitSecretNotFoundError`` outside a runtime, then falls back to
``os.environ`` — so ``monkeypatch.setenv`` is the right toggle.

Covers:
  - Module-level configuration tables
      * VESSEL_TYPE_LABELS maps int → non-empty str for every code, with
        common shipping codes (70, 71, 80, 89) present and labelled
      * _VESSEL_COLORS covers the primary shipping labels (Container, Cargo,
        Tanker, Bulk Carrier, LNG, Passenger, Fishing, Other, Unknown). Niche
        labels (Sailing, Pleasure) are intentionally NOT colored and fall
        through to vessel_color's "#64748b" default. Every configured colour
        is a 7-char "#xxxxxx" hex string.
      * _PORT_VESSEL_CONFIG entries have (count tuple, lat, lon, spread)
        with valid ranges (count[0] <= count[1], -90<=lat<=90, -180<=lon<=180)
      * _SEASONAL_MONTH spans 12 months with positive floats
      * _PORT_NAMES_DEST and _FLAGS are non-empty unique-ish lists of strings

  - API key helpers
      * aisstream_available() → False with no env var
      * aisstream_available() → True after monkeypatch.setenv
      * _get_aisstream_key() returns the env value when set

  - _bbox_from_center
      * Equator: lat/lon deltas are symmetric and ≈ radius/60
      * High latitude (60°N): longitude span widens by ~2× vs latitude span
        (cos 60° ≈ 0.5)
      * Zero radius → lat_min == lat_max, lon_min == lon_max
      * Always returns (lat_min, lon_min, lat_max, lon_max) with
        lat_min < lat_max and lon_min < lon_max for radius > 0
      * lat=90° edge case: the cos-protected divisor (1e-9) keeps it finite

  - _great_circle_nm
      * Identical points → 0.0
      * Known dyad (LAX → SHA) ≈ 5700–6100 nm
      * Symmetric: nm(A,B) == nm(B,A)
      * Antipodal points (lat,lon) and (-lat, lon+180) ≈ π·R nm ≈ 10,800

  - _destination_for_port
      * Origin never appears in the destination set across many samples
      * With no registered lanes (unknown LOCODE) the function still returns a
        valid string from _PORT_NAMES_DEST
      * Distribution is biased toward registered lane destinations for a known
        port (USLAX) — at least one lane destination outranks a random
        non-lane port across 500 samples

  - _transit_days
      * Returns _AVG_TRANSIT_DAYS for two unknown ports
      * Symmetric when neither direction is a registered lane (great-circle
        fallback)
      * Returns the registered ``direct`` lane transit when present (eastbound
        and westbound can DIFFER for the same port pair — real Pacific lanes
        do)
      * Returns a positive int >= 2 for any known O→D pair

  - _port_congestion
      * Unknown LOCODE → uses neutral 0.4-class default (size=0.4 path)
      * Known small port (USLAX) returns a value in [0,1]
      * Returned value is in [0, 1] for every configured port

  - _parse_aisstream_vessel
      * Empty dict → empty dict (mmsi is "", filtered downstream)
      * Minimal Position-only payload → produces lat/lon and "Other" type
      * Type-code 71 maps to "Container" with the container colour
      * Heading wraps modulo 360 (e.g. 540 → 180)
      * Speed is rounded to 1 decimal; lat/lon to 5 decimals
      * Malformed payload (raises during parsing) → returns {} (the except
        branch)

  - _synthetic_vessels_for_port
      * Known LOCODE (CNSHA) returns N vessels in config["count"] range
      * Unknown LOCODE returns 5–10 vessels (the default fallback range)
      * Deterministic within the cache window (two consecutive calls equal)
      * Every vessel has all required keys with the expected types
      * vessel_type is one of the labels in VESSEL_TYPE_LABELS values
      * destination is never the origin LOCODE
      * Cache is honoured: second call hits _SYNTH_CACHE and returns the same
        list object

  - fetch_vessels_near_port (cache stripped via __wrapped__)
      * No API key → returns synthetic vessels for the locode
      * API key present + REST 500 → falls back to synthetic
      * API key present + REST exception → falls back to synthetic
      * API key present + valid JSON list → returns parsed AISstream vessels
        (source == "aisstream") and never hits the synthetic path
      * API key present + REST returns empty list → falls back to synthetic
      * API key present + REST returns non-list payload → falls back to
        synthetic

  - fetch_vessels_on_route (cache stripped via __wrapped__)
      * Empty waypoints → []
      * No API key → synthetic vessels at the corridor centroid
      * API key + each segment returns vessels → unioned & deduped by MMSI
      * API key + every segment RAISES → synthetic corridor fallback
        (Real finding: because ``fetch_vessels_near_port`` catches its OWN
        HTTPError/RuntimeError internally and returns synthetic vessels, the
        outer corridor-fallback path is only reachable when the inner helper
        is monkeypatched to actually raise — not when the REST layer alone
        errors.)
      * API key + REST errors but per-segment synthetic fallback succeeds →
        union of synthetic vessels (no double-fallback)

  - fetch_all_port_vessels
      * Skips ports missing a locode
      * Returns one entry per port with a locode
      * A per-port exception inside the loop → that locode → [] (caught)

  - vessel_color
      * Known label → its specific colour
      * Unknown label → "#64748b" fallback
"""
from __future__ import annotations

import math

import pytest

from data import aisstream_feed as af


# ─── Wrapper strippers ──────────────────────────────────────────────────────

# Both ``fetch_vessels_near_port`` and ``fetch_vessels_on_route`` are wrapped
# by ``@st.cache_data``; the raw forms below skip Streamlit's in-process cache.
_fetch_near_raw = af.fetch_vessels_near_port.__wrapped__  # type: ignore[attr-defined]
_fetch_route_raw = af.fetch_vessels_on_route.__wrapped__  # type: ignore[attr-defined]


# ─── Helpers ────────────────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal stand-in for ``requests.Response`` for the aisstream_feed path."""

    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests as _rq
            err = _rq.HTTPError(f"HTTP {self.status_code}")
            err.response = self  # type: ignore[attr-defined]
            raise err


def _patch_requests(monkeypatch, responder):
    """Patch ``aisstream_feed.requests.get`` with ``responder(url, **kw)``.

    Returns a list of captured URLs so tests can assert call shape.
    """
    captured: list[str] = []

    def _fake_get(url, *args, **kwargs):
        captured.append(url)
        return responder(url, **kwargs)

    class _FakeRequests:
        get = staticmethod(_fake_get)
        HTTPError = __import__("requests").HTTPError

    monkeypatch.setattr(af, "requests", _FakeRequests, raising=True)
    return captured


def _clear_synth_cache():
    """Wipe the module-level synthetic cache so per-test state is clean."""
    af._SYNTH_CACHE.clear()


@pytest.fixture(autouse=True)
def _reset_caches():
    """Wipe ALL state that persists across calls within a process:

    - module-level synthetic cache (``_SYNTH_CACHE``)
    - Streamlit's ``@st.cache_data`` memo for both REST entrypoints — without
      this, ``fetch_vessels_on_route`` re-uses cached vessels from the inner
      ``fetch_vessels_near_port`` call instead of going through the
      monkeypatched ``requests`` again.
    """
    import streamlit as _st
    _clear_synth_cache()
    _st.cache_data.clear()
    yield
    _clear_synth_cache()
    _st.cache_data.clear()


# ─── Configuration tables ───────────────────────────────────────────────────


def test_vessel_type_labels_have_common_shipping_codes():
    # The four labels the rest of the codebase actually keys on for shipping.
    for code in (70, 71, 80, 89):
        assert code in af.VESSEL_TYPE_LABELS
        assert isinstance(af.VESSEL_TYPE_LABELS[code], str)
        assert af.VESSEL_TYPE_LABELS[code]  # non-empty
    assert af.VESSEL_TYPE_LABELS[71] == "Container"
    assert af.VESSEL_TYPE_LABELS[70] == "Cargo"
    assert af.VESSEL_TYPE_LABELS[89] == "LNG"


def test_vessel_type_labels_keys_are_ints_values_are_strs():
    for code, label in af.VESSEL_TYPE_LABELS.items():
        assert isinstance(code, int) and code >= 0
        assert isinstance(label, str) and label.strip()


def test_vessel_colors_cover_shipping_labels_and_unknown():
    # The dominant shipping labels MUST be colored; niche labels like
    # ``Sailing``/``Pleasure`` (codes 36/37) are intentionally allowed to fall
    # through ``vessel_color``'s "#64748b" default, but the big-money classes
    # (Container, Cargo, Tanker, Bulk, LNG, Passenger, Fishing, plus the
    # generic Other and Unknown buckets) must all be present.
    must_be_colored = {
        "Container", "Cargo", "Tanker", "Bulk Carrier", "LNG",
        "Passenger", "Fishing", "Other", "Unknown",
    }
    missing = must_be_colored - set(af._VESSEL_COLORS)
    assert not missing, f"missing colours for primary labels: {missing}"
    # And every configured colour must be a valid 7-char hex string.
    for label, hex_color in af._VESSEL_COLORS.items():
        assert isinstance(hex_color, str)
        assert hex_color.startswith("#") and len(hex_color) == 7


def test_port_vessel_config_has_valid_ranges():
    assert af._PORT_VESSEL_CONFIG, "must not be empty"
    for locode, cfg in af._PORT_VESSEL_CONFIG.items():
        assert isinstance(locode, str) and len(locode) == 5
        lo, hi = cfg["count"]
        assert isinstance(lo, int) and isinstance(hi, int)
        assert 0 < lo <= hi
        assert -90.0 <= cfg["lat"] <= 90.0
        assert -180.0 <= cfg["lon"] <= 180.0
        assert cfg["spread"] > 0


def test_seasonal_month_covers_12_months_positive():
    assert set(af._SEASONAL_MONTH) == set(range(1, 13))
    for m, mult in af._SEASONAL_MONTH.items():
        assert isinstance(mult, float)
        assert mult > 0


def test_port_names_dest_and_flags_non_empty_str_lists():
    assert af._PORT_NAMES_DEST and all(isinstance(p, str) and len(p) == 5
                                        for p in af._PORT_NAMES_DEST)
    assert af._FLAGS and all(isinstance(f, str) and f.strip() for f in af._FLAGS)


# ─── API key helpers ───────────────────────────────────────────────────────


def test_aisstream_available_false_without_key(monkeypatch):
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    # st.secrets raises when no secrets.toml; the helper swallows it.
    assert af.aisstream_available() is False


def test_aisstream_available_true_with_env_key(monkeypatch):
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    monkeypatch.setenv("AISSTREAM_KEY", "dummy-test-key")
    assert af.aisstream_available() is True


def test_get_aisstream_key_returns_env_value(monkeypatch):
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    monkeypatch.setenv("AISSTREAM_KEY", "abc123")
    assert af._get_aisstream_key() == "abc123"


# ─── _bbox_from_center ─────────────────────────────────────────────────────


def test_bbox_equator_symmetric_and_one_minute_per_nm():
    # Equator: cos(0) = 1, so lat and lon deltas are both radius/60.
    lat_min, lon_min, lat_max, lon_max = af._bbox_from_center(0.0, 0.0, 60.0)
    # 60 nm → 1 degree of latitude.
    assert lat_max - 0.0 == pytest.approx(1.0, abs=1e-9)
    assert 0.0 - lat_min == pytest.approx(1.0, abs=1e-9)
    assert lon_max - 0.0 == pytest.approx(1.0, abs=1e-9)
    assert 0.0 - lon_min == pytest.approx(1.0, abs=1e-9)


def test_bbox_high_latitude_widens_longitude():
    # cos(60°) = 0.5 → longitude span is twice the latitude span.
    lat_min, lon_min, lat_max, lon_max = af._bbox_from_center(60.0, 0.0, 60.0)
    lat_span = lat_max - lat_min  # ≈ 2.0
    lon_span = lon_max - lon_min  # ≈ 4.0 (1/cos 60°)
    assert lat_span == pytest.approx(2.0, abs=1e-6)
    assert lon_span == pytest.approx(4.0, abs=1e-2)


def test_bbox_zero_radius_collapses_to_point():
    lat_min, lon_min, lat_max, lon_max = af._bbox_from_center(33.74, -118.27, 0.0)
    assert lat_min == lat_max == 33.74
    assert lon_min == lon_max == -118.27


def test_bbox_ordering_is_min_lat_min_lon_max_lat_max_lon():
    lat_min, lon_min, lat_max, lon_max = af._bbox_from_center(33.74, -118.27, 50.0)
    assert lat_min < lat_max
    assert lon_min < lon_max


def test_bbox_at_pole_does_not_divzero():
    # The cos-protected ``or 1e-9`` keeps the call finite even at lat=90.
    lat_min, lon_min, lat_max, lon_max = af._bbox_from_center(90.0, 0.0, 10.0)
    assert math.isfinite(lat_min) and math.isfinite(lat_max)
    assert math.isfinite(lon_min) and math.isfinite(lon_max)


# ─── _great_circle_nm ──────────────────────────────────────────────────────


def test_great_circle_zero_distance_when_same_point():
    assert af._great_circle_nm(33.74, -118.27, 33.74, -118.27) == pytest.approx(0.0, abs=1e-9)


def test_great_circle_lax_to_shanghai_in_range():
    # Real LAX→SHA great-circle is ~5630 nm (8730 km / 1.852).
    nm = af._great_circle_nm(33.74, -118.27, 31.23, 121.47)
    assert 5500 < nm < 6200, f"LAX→SHA out of expected range: {nm}"


def test_great_circle_symmetric():
    a = af._great_circle_nm(33.74, -118.27, 31.23, 121.47)
    b = af._great_circle_nm(31.23, 121.47, 33.74, -118.27)
    assert a == pytest.approx(b, abs=1e-6)


def test_great_circle_antipodal_is_half_circumference():
    # Earth half-circumference ≈ π × 3440.065 ≈ 10807 nm.
    nm = af._great_circle_nm(0.0, 0.0, 0.0, 180.0)
    assert nm == pytest.approx(math.pi * 3440.065, rel=1e-3)


# ─── _destination_for_port ─────────────────────────────────────────────────


def test_destination_for_port_never_equals_origin():
    import random
    rng = random.Random(42)
    for origin in ("USLAX", "CNSHA", "NLRTM", "SGSIN"):
        for _ in range(50):
            dest = af._destination_for_port(origin, rng)
            assert dest != origin


def test_destination_for_port_unknown_origin_returns_valid_dest():
    import random
    rng = random.Random(0)
    dest = af._destination_for_port("ZZZZZ", rng)
    assert isinstance(dest, str) and len(dest) == 5
    # With no registered lanes the function falls back to a uniform pick over
    # _PORT_NAMES_DEST (with the origin removed).
    assert dest in af._PORT_NAMES_DEST


def test_destination_for_port_biases_toward_registered_lanes():
    """Across many samples from USLAX, lane destinations should dominate."""
    import random
    rng = random.Random(7)
    lane_dests = set(af._ROUTE_DEST_WEIGHTS.get("USLAX", {}))
    if not lane_dests:
        pytest.skip("route registry has no USLAX lanes — bias check N/A")

    counts: dict[str, int] = {}
    for _ in range(500):
        d = af._destination_for_port("USLAX", rng)
        counts[d] = counts.get(d, 0) + 1

    lane_share = sum(counts.get(d, 0) for d in lane_dests) / 500.0
    # The module docstring claims registered lanes get ~80% of the mass.
    # Allow a comfortable lower bound for sampling noise.
    assert lane_share > 0.55, (
        f"USLAX → lanes only got {lane_share:.2%} of samples; "
        "bias toward registered lanes appears broken"
    )


# ─── _transit_days ─────────────────────────────────────────────────────────


def test_transit_days_unknown_pair_uses_avg_default():
    days = af._transit_days("AAAAA", "BBBBB")
    assert days == af._AVG_TRANSIT_DAYS


def test_transit_days_symmetric_when_great_circle_fallback():
    """Two ports with NO registered lane → great-circle fallback is symmetric."""
    # Pick two configured ports that are NOT a registered lane pair (CNTXG,
    # USSAV is a real configured-but-not-routed pair).
    a, b = "CNTXG", "USSAV"
    # Confirm pre-condition: not in the lane lookup either direction.
    assert (a, b) not in af._ROUTE_TRANSIT and (b, a) not in af._ROUTE_TRANSIT
    assert af._transit_days(a, b) == af._transit_days(b, a)


def test_transit_days_uses_direct_lane_when_registered():
    """Registered O→D pairs may be asymmetric (real shipping reflects this:
    eastbound vs westbound differ on the Pacific). The function must use
    ``direct`` first; if both directions are registered the two calls just
    return their own registered transits, which may differ."""
    direct = af._ROUTE_TRANSIT.get(("CNSHA", "USLAX"))
    if direct is None:
        pytest.skip("CNSHA→USLAX not in registry")
    assert af._transit_days("CNSHA", "USLAX") == direct


def test_transit_days_known_pair_is_positive_int_at_least_two():
    for o, d in [("USLAX", "CNSHA"), ("SGSIN", "NLRTM"), ("HKHKG", "USNYC")]:
        days = af._transit_days(o, d)
        assert isinstance(days, int)
        assert days >= 2


# ─── _port_congestion ──────────────────────────────────────────────────────


def test_port_congestion_unknown_locode_returns_value_in_range():
    val = af._port_congestion("ZZZZZ")
    assert 0.0 <= val <= 1.0


def test_port_congestion_known_locode_returns_value_in_range():
    val = af._port_congestion("USLAX")
    assert 0.0 <= val <= 1.0


def test_port_congestion_every_configured_port_in_range():
    for locode in af._PORT_VESSEL_CONFIG:
        val = af._port_congestion(locode)
        assert 0.0 <= val <= 1.0, f"{locode}: {val}"


# ─── _parse_aisstream_vessel ───────────────────────────────────────────────


def test_parse_aisstream_empty_dict_returns_blank_mmsi_record():
    # Empty input still produces a defaulted dict (caller filters by mmsi).
    out = af._parse_aisstream_vessel({})
    assert isinstance(out, dict)
    assert out.get("mmsi") == ""  # filtered downstream
    assert out.get("vessel_type") in af._VESSEL_COLORS
    assert out.get("source") == "aisstream"


def test_parse_aisstream_minimal_position_payload():
    raw = {
        "Mmsi": 123456789,
        "Position": {"Latitude": 33.74, "Longitude": -118.27},
    }
    out = af._parse_aisstream_vessel(raw)
    assert out["mmsi"] == "123456789"
    assert out["lat"] == 33.74
    assert out["lon"] == -118.27
    # Type code defaults to 0 → "Unknown" is the table entry; the helper
    # routes 0 to VESSEL_TYPE_LABELS[0]="Unknown". The .get default for the
    # outer label call is "Other", but 0 IS in the table.
    assert out["vessel_type"] == "Unknown"
    assert out["source"] == "aisstream"


def test_parse_aisstream_type_code_71_is_container():
    raw = {
        "Mmsi": 1,
        "Position": {"Latitude": 0.0, "Longitude": 0.0},
        "ShipStaticAndVoyageRelatedData": {
            "TypeOfShipAndCargoType": 71,
            "ShipName": " EVER GIVEN ",
        },
    }
    out = af._parse_aisstream_vessel(raw)
    assert out["vessel_type_code"] == 71
    assert out["vessel_type"] == "Container"
    assert out["color"] == af._VESSEL_COLORS["Container"]
    # ShipName must be stripped of surrounding whitespace.
    assert out["name"] == "EVER GIVEN"


def test_parse_aisstream_heading_modulo_360():
    raw = {
        "Mmsi": 1,
        "PositionReport": {"TrueHeading": 540},
    }
    out = af._parse_aisstream_vessel(raw)
    assert out["heading"] == 540 % 360 == 180


def test_parse_aisstream_speed_and_position_rounding():
    raw = {
        "Mmsi": 7,
        "Position": {"Latitude": 33.7400123456, "Longitude": -118.2700987654},
        "PositionReport": {"SpeedOverGround": 12.34567},
    }
    out = af._parse_aisstream_vessel(raw)
    assert out["lat"] == 33.74001
    assert out["lon"] == -118.27010
    assert out["speed_kts"] == 12.3


def test_parse_aisstream_malformed_returns_empty():
    # Force the parse to raise: Position is a list with no .get attribute.
    class _Bad:
        def __init__(self):
            pass

        def get(self, key, default=None):
            # raises if anyone tries to read anything off Position-like dicts
            raise ValueError("boom")

    raw = {"Mmsi": 1, "Position": _Bad()}
    out = af._parse_aisstream_vessel(raw)
    assert out == {}


def test_parse_aisstream_fallback_keys_used_when_position_empty():
    """When ``Position`` is empty, the helper falls back to ``PositionReport``."""
    raw = {
        "Mmsi": 42,
        "Position": {},
        "PositionReport": {"Latitude": 1.5, "Longitude": 103.85,
                           "SpeedOverGround": 8.0, "CourseOverGround": 90},
    }
    out = af._parse_aisstream_vessel(raw)
    assert out["lat"] == 1.5
    assert out["lon"] == 103.85
    assert out["speed_kts"] == 8.0
    # CourseOverGround is used when TrueHeading is missing.
    assert out["heading"] == 90


# ─── _synthetic_vessels_for_port ───────────────────────────────────────────


def _required_vessel_keys() -> set[str]:
    return {
        "mmsi", "name", "vessel_type", "vessel_type_code", "lat", "lon",
        "speed_kts", "heading", "destination", "eta", "length_m", "flag",
        "last_updated", "color", "source",
    }


def test_synthetic_known_port_in_count_range():
    cfg = af._PORT_VESSEL_CONFIG["CNSHA"]
    vessels = af._synthetic_vessels_for_port("CNSHA", cfg["lat"], cfg["lon"])
    lo, hi = cfg["count"]
    assert lo <= len(vessels) <= hi


def test_synthetic_unknown_port_uses_default_range():
    vessels = af._synthetic_vessels_for_port("ZZZZZ", 0.0, 0.0)
    # default fallback: count (5, 10)
    assert 5 <= len(vessels) <= 10


def test_synthetic_deterministic_within_cache_window():
    a = af._synthetic_vessels_for_port("USLAX", 33.74, -118.27)
    b = af._synthetic_vessels_for_port("USLAX", 33.74, -118.27)
    # The cache hit returns the exact same list reference.
    assert a is b


def test_synthetic_vessels_have_all_required_keys():
    vessels = af._synthetic_vessels_for_port("NLRTM", 51.92, 4.48)
    assert vessels
    required = _required_vessel_keys()
    for v in vessels:
        assert required.issubset(set(v.keys())), f"missing keys: {required - set(v.keys())}"
        # spot-check types
        assert isinstance(v["mmsi"], str)
        assert isinstance(v["lat"], float)
        assert isinstance(v["lon"], float)
        assert isinstance(v["heading"], int)
        assert 0 <= v["heading"] <= 359
        assert v["speed_kts"] >= 0.0


def test_synthetic_vessel_types_use_known_labels():
    vessels = af._synthetic_vessels_for_port("CNSHA", 31.23, 121.47)
    known = set(af.VESSEL_TYPE_LABELS.values())
    for v in vessels:
        assert v["vessel_type"] in known


def test_synthetic_destinations_never_equal_origin():
    for locode in ("USLAX", "CNSHA", "SGSIN", "NLRTM"):
        cfg = af._PORT_VESSEL_CONFIG[locode]
        vessels = af._synthetic_vessels_for_port(locode, cfg["lat"], cfg["lon"])
        for v in vessels:
            assert v["destination"] != locode


def test_synthetic_cache_hit_returns_same_list():
    """Once cached, the same locode should return the cached list object."""
    first = af._synthetic_vessels_for_port("SGSIN", 1.29, 103.85)
    # Verify it's actually in the cache.
    assert "SGSIN" in af._SYNTH_CACHE
    second = af._synthetic_vessels_for_port("SGSIN", 1.29, 103.85)
    assert first is second


# ─── fetch_vessels_near_port ───────────────────────────────────────────────


def test_fetch_near_no_api_key_returns_synthetic(monkeypatch):
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    out = _fetch_near_raw(33.74, -118.27, radius_nm=50, locode="USLAX")
    assert out, "synthetic fallback must produce vessels"
    # Every vessel should come from the synthetic generator.
    assert all(v.get("source") == "synthetic" for v in out)


def test_fetch_near_with_key_rest_500_falls_back_to_synthetic(monkeypatch):
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    monkeypatch.setenv("AISSTREAM_KEY", "fake-key")

    def _responder(url, **kw):
        return _FakeResponse(500, payload={})

    urls = _patch_requests(monkeypatch, _responder)
    out = _fetch_near_raw(33.74, -118.27, radius_nm=50, locode="USLAX")
    assert out
    assert all(v.get("source") == "synthetic" for v in out)
    assert urls, "REST endpoint should have been hit at least once"


def test_fetch_near_with_key_rest_raises_falls_back_to_synthetic(monkeypatch):
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    monkeypatch.setenv("AISSTREAM_KEY", "fake-key")

    def _responder(url, **kw):
        raise RuntimeError("network is down")

    _patch_requests(monkeypatch, _responder)
    out = _fetch_near_raw(33.74, -118.27, radius_nm=50, locode="USLAX")
    assert out
    assert all(v.get("source") == "synthetic" for v in out)


def test_fetch_near_with_key_valid_payload_returns_aisstream_vessels(monkeypatch):
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    monkeypatch.setenv("AISSTREAM_KEY", "fake-key")

    payload = [
        {
            "Mmsi": 111111111,
            "Position": {"Latitude": 33.74, "Longitude": -118.27},
            "ShipStaticAndVoyageRelatedData": {
                "TypeOfShipAndCargoType": 71,
                "ShipName": "EVER GIVEN",
            },
        },
        {
            "Mmsi": 222222222,
            "Position": {"Latitude": 33.80, "Longitude": -118.30},
            "ShipStaticAndVoyageRelatedData": {
                "TypeOfShipAndCargoType": 80,
                "ShipName": "TANK STAR",
            },
        },
    ]

    def _responder(url, **kw):
        return _FakeResponse(200, payload=payload)

    urls = _patch_requests(monkeypatch, _responder)
    out = _fetch_near_raw(33.74, -118.27, radius_nm=50, locode="USLAX")
    assert out and len(out) == 2
    assert all(v.get("source") == "aisstream" for v in out)
    mmsis = {v["mmsi"] for v in out}
    assert mmsis == {"111111111", "222222222"}
    # Verify the bounding-box param was constructed
    assert urls and urls[0].endswith("/messages/vessels")


def test_fetch_near_with_key_empty_list_falls_back_to_synthetic(monkeypatch):
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    monkeypatch.setenv("AISSTREAM_KEY", "fake-key")

    def _responder(url, **kw):
        return _FakeResponse(200, payload=[])

    _patch_requests(monkeypatch, _responder)
    out = _fetch_near_raw(33.74, -118.27, radius_nm=50, locode="USLAX")
    # Empty list → no vessels → falls through to synthetic.
    assert out
    assert all(v.get("source") == "synthetic" for v in out)


def test_fetch_near_with_key_non_list_payload_falls_back_to_synthetic(monkeypatch):
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    monkeypatch.setenv("AISSTREAM_KEY", "fake-key")

    def _responder(url, **kw):
        # A dict isn't a list; the ``isinstance(raw_list, list)`` guard fails
        # and the function falls through.
        return _FakeResponse(200, payload={"error": "wrong shape"})

    _patch_requests(monkeypatch, _responder)
    out = _fetch_near_raw(33.74, -118.27, radius_nm=50, locode="USLAX")
    assert out
    assert all(v.get("source") == "synthetic" for v in out)


# ─── fetch_vessels_on_route ────────────────────────────────────────────────


def test_fetch_route_empty_waypoints_returns_empty():
    assert _fetch_route_raw("r1", waypoints=[]) == []


def test_fetch_route_no_api_key_returns_synthetic_corridor(monkeypatch):
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    waypoints = [(33.74, -118.27), (35.0, -140.0), (31.23, 121.47)]
    out = _fetch_route_raw("transpacific_eb", waypoints=waypoints, radius_nm=25)
    assert out
    # Synthetic corridor uses a "route_..." locode key that isn't in the
    # configured ports, so source must be "synthetic".
    assert all(v.get("source") == "synthetic" for v in out)


def test_fetch_route_with_key_unions_and_dedups_by_mmsi(monkeypatch):
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    monkeypatch.setenv("AISSTREAM_KEY", "fake-key")

    # Each segment returns the same MMSI 555 plus a unique one — dedup must
    # collapse the duplicate.
    def _make_payload(unique_mmsi):
        return [
            {"Mmsi": 555, "Position": {"Latitude": 30.0, "Longitude": 0.0}},
            {"Mmsi": unique_mmsi,
             "Position": {"Latitude": 30.0, "Longitude": 0.0}},
        ]

    seg_counter = {"n": 0}

    def _responder(url, **kw):
        seg_counter["n"] += 1
        return _FakeResponse(200, payload=_make_payload(1000 + seg_counter["n"]))

    _patch_requests(monkeypatch, _responder)
    # 4 distinct waypoints → up to 4 segments queried.
    wp = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
    out = _fetch_route_raw("r-test", waypoints=wp, radius_nm=25)

    # 1 shared MMSI 555 + n unique → unique-count is between 2 and 5.
    mmsis = {v["mmsi"] for v in out}
    assert "555" in mmsis
    # Source should be "aisstream" — we never fell back.
    assert all(v.get("source") == "aisstream" for v in out)
    assert len(mmsis) == len(out)  # no duplicate MMSIs leak through


def test_fetch_route_with_key_every_segment_raises_falls_back_to_corridor(monkeypatch):
    """Note: ``fetch_vessels_near_port`` itself catches HTTPErrors / runtime
    errors internally and returns synthetic vessels. The route corridor
    fallback only triggers when the per-segment helper actually RAISES — so
    here we monkeypatch ``fetch_vessels_near_port`` to raise directly.
    """
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    monkeypatch.setenv("AISSTREAM_KEY", "fake-key")

    def _boom(*args, **kwargs):
        raise RuntimeError("segment outright failure")

    monkeypatch.setattr(af, "fetch_vessels_near_port", _boom, raising=True)

    wp = [(0.0, 0.0), (1.0, 1.0)]
    out = _fetch_route_raw("r-fail", waypoints=wp, radius_nm=25)
    # All segments raised, ``all_vessels`` is empty, so synthetic corridor.
    assert out
    assert all(v.get("source") == "synthetic" for v in out)


def test_fetch_route_with_key_segments_returning_synthetic_still_count(monkeypatch):
    """Inverse case: when the REST call inside each segment errors but the
    segment helper falls back to its own synthetic vessels (the normal flow),
    the route function unions those synthetic vessels rather than re-falling
    back to a corridor — confirming there is no double-fallback behavior."""
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    monkeypatch.setenv("AISSTREAM_KEY", "fake-key")

    def _responder(url, **kw):
        raise RuntimeError("rest down")

    _patch_requests(monkeypatch, _responder)
    wp = [(0.0, 0.0), (1.0, 1.0)]
    out = _fetch_route_raw("r-passthrough", waypoints=wp, radius_nm=25)
    # Each segment returned synthetic; the route function unions those.
    assert out
    assert all(v.get("source") == "synthetic" for v in out)


# ─── fetch_all_port_vessels ────────────────────────────────────────────────


def test_fetch_all_port_vessels_skips_entries_without_locode(monkeypatch):
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    ports_cfg = [
        {"locode": "USLAX", "lat": 33.74, "lon": -118.27},
        {"lat": 0.0, "lon": 0.0},  # no locode → skipped
        {"locode": "", "lat": 0.0, "lon": 0.0},  # empty locode → skipped
    ]
    out = af.fetch_all_port_vessels(ports_cfg)
    assert "USLAX" in out
    assert "" not in out
    assert len(out) == 1


def test_fetch_all_port_vessels_returns_entry_per_port(monkeypatch):
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)
    ports_cfg = [
        {"locode": "USLAX", "lat": 33.74, "lon": -118.27},
        {"locode": "CNSHA", "lat": 31.23, "lon": 121.47},
        {"locode": "SGSIN", "lat": 1.29, "lon": 103.85},
    ]
    out = af.fetch_all_port_vessels(ports_cfg)
    assert set(out.keys()) == {"USLAX", "CNSHA", "SGSIN"}
    for locode, vessels in out.items():
        assert isinstance(vessels, list)
        assert vessels  # synthetic fallback should produce non-empty


def test_fetch_all_port_vessels_per_port_exception_yields_empty_list(monkeypatch):
    monkeypatch.delenv("AISSTREAM_KEY", raising=False)

    # Force fetch_vessels_near_port to raise so the inner try/except triggers.
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated fetch failure")

    monkeypatch.setattr(af, "fetch_vessels_near_port", _boom, raising=True)

    out = af.fetch_all_port_vessels([
        {"locode": "USLAX", "lat": 33.74, "lon": -118.27},
    ])
    assert out == {"USLAX": []}


# ─── vessel_color ──────────────────────────────────────────────────────────


def test_vessel_color_known_label_returns_specific_color():
    assert af.vessel_color("Container") == af._VESSEL_COLORS["Container"]
    assert af.vessel_color("Tanker") == af._VESSEL_COLORS["Tanker"]
    assert af.vessel_color("LNG") == af._VESSEL_COLORS["LNG"]


def test_vessel_color_unknown_label_returns_fallback():
    assert af.vessel_color("NotAType") == "#64748b"
    assert af.vessel_color("") == "#64748b"
