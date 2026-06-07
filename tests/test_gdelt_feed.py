"""Tests for the keyless GDELT geo-event feed (rec R014).

OFFLINE-SAFE: no network. ``fetch_chokepoint_events`` takes an injectable
``http_get`` so every test feeds a fixture payload (or a raising stub to
exercise the offline path). The pure helpers (``events_near``,
``gdelt_chokepoint_signal``, ``escalate``) need no I/O at all.
"""
from __future__ import annotations

import pytest

from data.gdelt_feed import (
    GdeltEvent,
    escalate,
    events_near,
    fetch_chokepoint_events,
    gdelt_chokepoint_signal,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

class _Resp:
    """Minimal stand-in for a requests.Response."""
    def __init__(self, payload=None, *, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text else ("{}" if payload is None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Cp:
    """Tiny chokepoint-like with lat/lon/name."""
    def __init__(self, lat, lon, name="x"):
        self.lat, self.lon, self.name = lat, lon, name


def _ev(lat, lon, tone=-5.0):
    return GdeltEvent(lat=lat, lon=lon, title="t", url="u", seen_at="", tone=tone)


# ── events_near (pure geo-filter) ─────────────────────────────────────────────

def test_events_near_includes_point_inside_radius() -> None:
    # ~11 km north of the center — well inside a 50 km radius.
    inside = _ev(30.1, 32.5)
    assert events_near(30.0, 32.5, 50.0, [inside]) == [inside]


def test_events_near_excludes_point_outside_radius() -> None:
    # ~110 km away — outside a 50 km radius.
    outside = _ev(31.0, 32.5)
    assert events_near(30.0, 32.5, 50.0, [outside]) == []


def test_events_near_drops_events_without_coords() -> None:
    bad = GdeltEvent(lat=None, lon=None, title="t", url="u", seen_at="", tone=-5.0)  # type: ignore[arg-type]
    assert events_near(30.0, 32.5, 500.0, [bad]) == []


def test_events_near_accepts_dicts() -> None:
    near = [{"lat": 30.05, "lon": 32.52, "tone": -3.0}]
    assert len(events_near(30.0, 32.5, 50.0, near)) == 1


# ── gdelt_chokepoint_signal (event → risk) ───────────────────────────────────

def test_signal_no_events_is_dark() -> None:
    """Absence of events is NEVER escalation — realness 'dark', (None, None)."""
    level, dtype, realness = gdelt_chokepoint_signal(_Cp(30.0, 32.5), [])
    assert (level, dtype, realness) == (None, None, "dark")


def test_signal_dense_negative_cluster_is_critical() -> None:
    cp = _Cp(30.0, 32.5)
    cluster = [_ev(30.0 + 0.01 * i, 32.5, tone=-6.0) for i in range(10)]
    level, dtype, realness = gdelt_chokepoint_signal(cp, cluster, radius_km=300.0)
    assert level == "CRITICAL"
    assert dtype == "ACTIVE_CONFLICT"
    assert realness == "live"


def test_signal_clustered_adverse_is_high() -> None:
    cp = _Cp(30.0, 32.5)
    cluster = [_ev(30.0 + 0.01 * i, 32.5, tone=-3.0) for i in range(5)]
    level, _, realness = gdelt_chokepoint_signal(cp, cluster, radius_km=300.0)
    assert level == "HIGH" and realness == "live"


def test_signal_real_but_benign_is_low_not_dark() -> None:
    """Real events with positive tone → a real LOW read, not 'dark'."""
    cp = _Cp(30.0, 32.5)
    benign = [_ev(30.0, 32.5, tone=3.0), _ev(30.01, 32.5, tone=2.0)]
    level, dtype, realness = gdelt_chokepoint_signal(cp, benign, radius_km=300.0)
    assert realness == "live"
    assert level == "LOW" and dtype == "NONE"


def test_signal_far_events_do_not_escalate() -> None:
    """A dense negative cluster on the far side of the planet → dark."""
    cp = _Cp(30.0, 32.5)
    far = [_ev(-40.0, -120.0, tone=-8.0) for _ in range(12)]
    assert gdelt_chokepoint_signal(cp, far, radius_km=250.0) == (None, None, "dark")


# ── escalate (never downgrades) ──────────────────────────────────────────────

def test_escalate_raises_to_worse_level() -> None:
    assert escalate("HIGH", "CRITICAL") == "CRITICAL"


def test_escalate_never_downgrades() -> None:
    assert escalate("CRITICAL", "LOW") == "CRITICAL"
    assert escalate("HIGH", "MODERATE") == "HIGH"


# ── fetch_chokepoint_events (offline-safe + injectable) ──────────────────────

def test_fetch_offline_returns_empty_unavailable_without_raising() -> None:
    """Every fetch raising (offline) → ([], 'unavailable'), never an exception."""
    def _boom(*_a, **_k):
        raise ConnectionError("offline")

    events, realness = fetch_chokepoint_events(
        [("suez", _Cp(30.0, 32.5))], http_get=_boom, cache_ttl_hours=0.0,
    )
    assert events == [] and realness == "unavailable"


def test_fetch_parses_doc_artlist_payload() -> None:
    payload = {"articles": [
        {"title": "Red Sea strike", "url": "http://x", "seendate": "20260601",
         "tone": -6.1},
        {"title": "Carrier diverts", "url": "http://y", "seendate": "20260601"},
    ]}

    def _get(url, **_k):
        return _Resp(payload)

    events, realness = fetch_chokepoint_events(
        [("suez", _Cp(30.0, 32.5))], http_get=_get, cache_ttl_hours=0.0,
    )
    assert realness == "live"
    assert len(events) == 2
    # Articles inherit the chokepoint coords (DOC near: restricts host-side).
    assert all(abs(e.lat - 30.0) < 1e-9 for e in events)
    assert events[0].tone == pytest.approx(-6.1)
    assert events[1].tone == 0.0  # missing tone defaults to 0


def test_fetch_parses_geojson_payload() -> None:
    payload = {"features": [
        {"geometry": {"type": "Point", "coordinates": [32.5, 30.0]},
         "properties": {"name": "Suez", "tone": -4.0, "count": 3}},
    ]}

    def _get(url, **_k):
        return _Resp(payload)

    events, realness = fetch_chokepoint_events(
        [("suez", _Cp(30.0, 32.5))], http_get=_get, cache_ttl_hours=0.0,
    )
    assert realness == "live"
    # count=3 fans out to 3 events at [lon=32.5, lat=30.0].
    assert len(events) == 3
    assert all(abs(e.lon - 32.5) < 1e-9 and abs(e.lat - 30.0) < 1e-9 for e in events)


def test_fetch_real_but_empty_is_empty_realness() -> None:
    def _get(url, **_k):
        return _Resp({"articles": []})

    events, realness = fetch_chokepoint_events(
        [("suez", _Cp(30.0, 32.5))], http_get=_get, cache_ttl_hours=0.0,
    )
    assert events == [] and realness == "empty"


def test_fetch_rate_limit_text_is_unavailable() -> None:
    """A 200 with GDELT's plaintext rate-limit notice is treated as a failure."""
    def _get(url, **_k):
        return _Resp(None, status_code=200,
                     text="Please limit requests to one every 5 seconds")

    events, realness = fetch_chokepoint_events(
        [("suez", _Cp(30.0, 32.5))], http_get=_get, cache_ttl_hours=0.0,
    )
    assert events == [] and realness == "unavailable"


def test_fetch_non_200_is_unavailable() -> None:
    def _get(url, **_k):
        return _Resp({"articles": [{"title": "x"}]}, status_code=404)

    events, realness = fetch_chokepoint_events(
        [("suez", _Cp(30.0, 32.5))], http_get=_get, cache_ttl_hours=0.0,
    )
    assert events == [] and realness == "unavailable"
