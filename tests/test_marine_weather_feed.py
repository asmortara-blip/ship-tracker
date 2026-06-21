"""Tests for the keyless Open-Meteo marine-weather feed + its weather-risk blend
(rec R032).

OFFLINE-SAFE: no network. ``fetch_marine_conditions`` / ``fetch_route_weather``
take an injectable ``http_get`` so every test feeds a fixture Open-Meteo payload
(or a raising stub to exercise the offline path). The pure mappers
(``conditions_to_risk``, the ``_ramp`` helper) need no I/O at all.

The blend in ``processing.weather_risk.compute_route_weather_risk`` is OPT-IN and
default-OFF, so the existing static-almanac behaviour is pinned here as a
regression (and re-checked by tests/test_weather_risk.py).
"""
from __future__ import annotations

import pytest

import data.marine_weather_feed as mwf
from data.marine_weather_feed import (
    MarineConditions,
    conditions_to_risk,
    fetch_marine_conditions,
    fetch_route_weather,
)
from processing.weather_risk import ALL_ROUTE_IDS, compute_route_weather_risk


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Redirect the feed's file cache to a per-test tmp dir.

    The ``live=True`` blend path uses the module-default ``cache_ttl_hours=6.0``,
    so without isolation one test's cached reading could be read back by the
    next (a real cross-test contamination this fixture prevents) — and tests must
    never write into the repo's ``cache/`` tree."""
    monkeypatch.setattr(mwf, "_CACHE_DIR", tmp_path / "marine")
    (tmp_path / "marine").mkdir()

class _Resp:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, payload=None, *, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _marine_payload(wave=None, wind_wave=None, swell=None):
    return {
        "latitude": 30.0,
        "longitude": 32.5,
        "current_units": {"wave_height": "m"},
        "current": {
            "time": "2026-06-08T00:00",
            "wave_height": wave,
            "wind_wave_height": wind_wave,
            "swell_wave_height": swell,
        },
    }


def _forecast_payload(wind=None, gusts=None):
    return {
        "latitude": 30.0,
        "longitude": 32.5,
        "current_units": {"wind_speed_10m": "km/h"},
        "current": {
            "time": "2026-06-08T00:00",
            "wind_speed_10m": wind,
            "wind_gusts_10m": gusts,
        },
    }


def _router(*, marine=None, forecast=None, boom=False):
    """Build an http_get stub that dispatches Marine vs Forecast by URL.

    ``marine`` / ``forecast`` are the JSON dicts (or None to simulate a
    failed/non-dict body). ``boom=True`` raises on every call (offline)."""

    def _get(url, *, params=None, headers=None, timeout=None):
        if boom:
            raise ConnectionError("offline")
        if "marine-api" in url:
            return _Resp(marine)
        return _Resp(forecast)

    return _get


# ── fetch_marine_conditions: parsing + provenance ─────────────────────────────


def test_fetch_parses_open_meteo_payload_and_is_real() -> None:
    """A real Marine + Forecast 200 parses into MarineConditions with is_real."""
    get = _router(
        marine=_marine_payload(wave=2.4, wind_wave=1.1, swell=2.0),
        forecast=_forecast_payload(wind=45.0, gusts=70.0),
    )
    c = fetch_marine_conditions(30.0, 32.5, http_get=get, cache_ttl_hours=0.0)
    assert c is not None
    assert c.is_real is True
    assert c.wave_height_m == pytest.approx(2.4)
    assert c.wind_wave_height_m == pytest.approx(1.1)
    assert c.swell_wave_height_m == pytest.approx(2.0)
    assert c.wind_speed_kmh == pytest.approx(45.0)
    assert c.wind_gusts_kmh == pytest.approx(70.0)


def test_fetch_offline_returns_none_without_raising() -> None:
    """A raising getter (offline) yields None — no exception, no block."""
    get = _router(boom=True)
    c = fetch_marine_conditions(30.0, 32.5, http_get=get, cache_ttl_hours=0.0)
    assert c is None


def test_fetch_inland_all_null_reading_is_none() -> None:
    """An inland point (Open-Meteo returns all-null waves AND no wind) is dark."""
    get = _router(
        marine=_marine_payload(wave=None, wind_wave=None, swell=None),
        forecast=_forecast_payload(wind=None, gusts=None),
    )
    c = fetch_marine_conditions(30.0, 32.5, http_get=get, cache_ttl_hours=0.0)
    assert c is None


def test_fetch_partial_null_waves_still_usable_via_wind() -> None:
    """Null waves but real wind (inland canal w/ wind grid) still yields a reading."""
    get = _router(
        marine=_marine_payload(wave=None, wind_wave=None, swell=None),
        forecast=_forecast_payload(wind=30.0, gusts=55.0),
    )
    c = fetch_marine_conditions(30.0, 32.5, http_get=get, cache_ttl_hours=0.0)
    assert c is not None
    assert c.wave_height_m is None
    assert c.wind_speed_kmh == pytest.approx(30.0)


def test_fetch_non_200_is_none() -> None:
    def _get(url, *, params=None, headers=None, timeout=None):
        return _Resp(None, status_code=503)

    c = fetch_marine_conditions(30.0, 32.5, http_get=_get, cache_ttl_hours=0.0)
    assert c is None


def test_fetch_marine_down_but_wind_up_is_usable() -> None:
    """Marine endpoint dead, forecast endpoint alive → still a usable reading."""
    def _get(url, *, params=None, headers=None, timeout=None):
        if "marine-api" in url:
            raise ConnectionError("marine down")
        return _Resp(_forecast_payload(wind=40.0, gusts=60.0))

    c = fetch_marine_conditions(30.0, 32.5, http_get=_get, cache_ttl_hours=0.0)
    assert c is not None
    assert c.wave_height_m is None
    assert c.wind_speed_kmh == pytest.approx(40.0)


# ── conditions_to_risk: monotonic + clamped ───────────────────────────────────


def _cond(wave=None, wind=None, gusts=None):
    return MarineConditions(
        lat=0.0, lon=0.0,
        wave_height_m=wave, wind_wave_height_m=None, swell_wave_height_m=None,
        wind_speed_kmh=wind, wind_gusts_kmh=gusts,
        fetched_at="", is_real=True,
    )


def test_conditions_to_risk_in_unit_interval() -> None:
    for wave in (0.0, 1.0, 3.0, 6.0, 12.0):
        for wind in (0.0, 20.0, 90.0, 200.0):
            r = conditions_to_risk(_cond(wave=wave, wind=wind))
            assert r is not None
            assert 0.0 <= r <= 1.0


def test_conditions_to_risk_monotonic_in_wave_height() -> None:
    """Higher waves never lower the score (holding wind fixed)."""
    prev = -1.0
    for wave in (0.5, 1.0, 2.0, 4.0, 6.0, 10.0):
        r = conditions_to_risk(_cond(wave=wave, wind=30.0))
        assert r is not None
        assert r >= prev, wave
        prev = r


def test_conditions_to_risk_monotonic_in_wind() -> None:
    prev = -1.0
    for wind in (10.0, 20.0, 40.0, 60.0, 90.0, 150.0):
        r = conditions_to_risk(_cond(wave=1.0, wind=wind))
        assert r is not None
        assert r >= prev, wind
        prev = r


def test_conditions_to_risk_calm_is_zero() -> None:
    """At/below the calm floor on every driver the score is 0."""
    r = conditions_to_risk(_cond(wave=0.5, wind=10.0, gusts=20.0))
    assert r == pytest.approx(0.0)


def test_conditions_to_risk_severe_is_one() -> None:
    """At/above the severe ceiling on any driver the score saturates to 1."""
    r = conditions_to_risk(_cond(wave=8.0, wind=30.0, gusts=40.0))
    assert r == pytest.approx(1.0)


def test_conditions_to_risk_takes_worst_driver() -> None:
    """MAX over drivers — a single severe driver dominates a calm one."""
    r = conditions_to_risk(_cond(wave=0.5, wind=10.0, gusts=200.0))
    assert r == pytest.approx(1.0)


def test_conditions_to_risk_none_inputs() -> None:
    assert conditions_to_risk(None) is None
    # all-None drivers → no signal → None
    assert conditions_to_risk(_cond()) is None


# ── fetch_route_weather: route → waypoint → score ─────────────────────────────


def test_fetch_route_weather_high_reading() -> None:
    """A high-sea-state reading at the route's waypoints yields a high score."""
    get = _router(
        marine=_marine_payload(wave=7.0),
        forecast=_forecast_payload(wind=100.0, gusts=130.0),
    )
    score = fetch_route_weather("asia_europe", http_get=get, cache_ttl_hours=0.0)
    assert score is not None
    assert score == pytest.approx(1.0)


def test_fetch_route_weather_offline_is_none() -> None:
    get = _router(boom=True)
    assert fetch_route_weather("asia_europe", http_get=get, cache_ttl_hours=0.0) is None


def test_fetch_route_weather_no_waypoints_is_none() -> None:
    """A route with no chokepoint coverage has no waypoints → None (modeled only)."""
    get = _router(marine=_marine_payload(wave=7.0), forecast=_forecast_payload(wind=100.0))
    assert fetch_route_weather("longbeach_to_asia", http_get=get, cache_ttl_hours=0.0) is None


# ── blend in compute_route_weather_risk ───────────────────────────────────────


def test_default_path_is_modeled_only() -> None:
    """No live data → is_real False, provenance 'modeled', no modeled_risk_score."""
    for route_id in ALL_ROUTE_IDS:
        idx = compute_route_weather_risk(route_id)
        assert idx.is_real is False, route_id
        assert idx.provenance == "modeled", route_id
        assert idx.modeled_risk_score is None, route_id


def test_blend_with_live_score_escalates_not_lowers() -> None:
    """A high live_score raises the score; a low one never lowers the modeled."""
    for route_id in ALL_ROUTE_IDS:
        base = compute_route_weather_risk(route_id)

        hi = compute_route_weather_risk(route_id, live_score=0.99)
        assert hi.current_risk_score >= base.current_risk_score, route_id
        assert hi.current_risk_score <= 1.0, route_id

        lo = compute_route_weather_risk(route_id, live_score=0.0)
        # escalate-only: a calm reading cannot drop below the climatology
        assert lo.current_risk_score == base.current_risk_score, route_id


def test_blend_stamps_provenance_and_preserves_modeled() -> None:
    base = compute_route_weather_risk("asia_europe")
    blended = compute_route_weather_risk("asia_europe", live_score=0.95)
    assert blended.is_real is True
    assert blended.provenance == "blended"
    assert blended.modeled_risk_score == pytest.approx(base.current_risk_score)
    assert blended.current_risk_score == pytest.approx(0.95)


def test_blend_via_live_flag_with_stub_http_get() -> None:
    """live=True + injected http_get fetches the route's waypoints offline-safe."""
    get = _router(
        marine=_marine_payload(wave=7.0),
        forecast=_forecast_payload(wind=110.0, gusts=140.0),
    )
    base = compute_route_weather_risk("asia_europe")
    blended = compute_route_weather_risk("asia_europe", live=True, http_get=get)
    assert blended.is_real is True
    assert blended.provenance == "blended"
    assert blended.current_risk_score >= base.current_risk_score
    assert blended.current_risk_score == pytest.approx(1.0)


def test_blend_live_flag_offline_falls_back_to_modeled() -> None:
    """live=True but the feed is dark → modeled-only, no raise."""
    get = _router(boom=True)
    base = compute_route_weather_risk("asia_europe")
    out = compute_route_weather_risk("asia_europe", live=True, http_get=get)
    assert out.is_real is False
    assert out.provenance == "modeled"
    assert out.current_risk_score == pytest.approx(base.current_risk_score)


def test_blend_does_not_change_unknown_route() -> None:
    """An unknown route stays a zero modeled index even with live opt-in."""
    idx = compute_route_weather_risk("no_such_route_xyz", live_score=0.9)
    # No events → early zero return; the blend never runs.
    assert idx.current_risk_score == 0.0
    assert idx.is_real is False
    assert idx.provenance == "modeled"
