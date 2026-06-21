"""Tests for the keyless GDELT disruption-news-VOLUME feed (rec R014).

OFFLINE-SAFE: no network. ``fetch_chokepoint_events`` takes an injectable
``http_get`` so every test feeds a fixture payload (or a raising stub to
exercise the offline path). The pure helpers (``events_near``,
``gdelt_chokepoint_signal``, ``escalate``) need no I/O at all.

The signal is a COUNTRY-LEVEL disruption-news VOLUME read (no geolocation, no
tone): the COUNT of disruption articles for a chokepoint's ``sourcecountry`` in
the window is binned to a risk level. These tests drive that count.
"""
from __future__ import annotations

import re

import pytest

from data.gdelt_feed import (
    GdeltEvent,
    _build_doc_url,
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


def _ev(lat, lon, key=""):
    """One disruption-news article stamped at (lat, lon) for a chokepoint."""
    return GdeltEvent(lat=lat, lon=lon, title="t", url="u", seen_at="",
                      chokepoint_key=key)


def _articles(n):
    """A DOC artlist payload with ``n`` disruption articles."""
    return {"articles": [
        {"title": f"disruption {i}", "url": f"http://x/{i}",
         "seendate": "20260601"} for i in range(n)
    ]}


# ── events_near (pure geo-filter / coord attribution) ─────────────────────────

def test_events_near_includes_point_inside_radius() -> None:
    inside = _ev(30.1, 32.5)
    assert events_near(30.0, 32.5, 50.0, [inside]) == [inside]


def test_events_near_excludes_point_outside_radius() -> None:
    outside = _ev(31.0, 32.5)
    assert events_near(30.0, 32.5, 50.0, [outside]) == []


def test_events_near_drops_events_without_coords() -> None:
    bad = GdeltEvent(lat=None, lon=None, title="t", url="u", seen_at="")  # type: ignore[arg-type]
    assert events_near(30.0, 32.5, 500.0, [bad]) == []


def test_events_near_accepts_dicts() -> None:
    near = [{"lat": 30.05, "lon": 32.52}]
    assert len(events_near(30.0, 32.5, 50.0, near)) == 1


# ── _build_doc_url is a VALID DOC country query (NOT the dead geo near:) ──────

def test_build_doc_url_is_a_valid_sourcecountry_query() -> None:
    url = _build_doc_url(("EG",), "14d")
    assert "sourcecountry:EG" in url or "sourcecountry%3AEG" in url
    # The disruption terms must be present (url-encoded).
    assert "attack" in url and "blockade" in url
    assert "mode=artlist" in url and "format=json" in url
    assert "timespan=14d" in url and "maxrecords=250" in url


def test_build_doc_url_has_no_invalid_geo_near_token() -> None:
    """The dead, invalid geo operator ``near<km>:<lat>,<lon>`` must be GONE.

    DOC's ``near`` is a TEXT-proximity operator, not geographic; the old query
    silently returned a plain-text error that masqueraded as an empty fetch.
    """
    url = _build_doc_url(("EG",), "14d")
    # No ``near<digits>:<lat>,<lon>`` geo token anywhere (raw or url-encoded).
    geo_near = re.compile(r"near\d+(?:%3A|:)-?\d+(?:\.\d+)?(?:%2C|,)-?\d+", re.I)
    assert geo_near.search(url) is None


def test_build_doc_url_ors_multiple_countries() -> None:
    url = _build_doc_url(("MY", "SG", "ID"), "14d")
    for cc in ("MY", "SG", "ID"):
        assert f"sourcecountry:{cc}" in url or f"sourcecountry%3A{cc}" in url
    assert "OR" in url  # the codes are OR-ed


# ── gdelt_chokepoint_signal (VOLUME → risk) ──────────────────────────────────

def test_signal_no_events_is_dark() -> None:
    """Absence of articles is NEVER escalation — realness 'dark', (None, None)."""
    level, dtype, realness = gdelt_chokepoint_signal(_Cp(30.0, 32.5), [])
    assert (level, dtype, realness) == (None, None, "dark")


def test_signal_huge_volume_is_critical() -> None:
    cp = _Cp(30.0, 32.5)
    cluster = [_ev(30.0, 32.5) for _ in range(120)]   # n >= 120
    level, dtype, realness = gdelt_chokepoint_signal(cp, cluster, radius_km=300.0)
    assert level == "CRITICAL"
    assert dtype == "ACTIVE_CONFLICT"
    assert realness == "live"


def test_signal_high_volume_is_high() -> None:
    cp = _Cp(30.0, 32.5)
    cluster = [_ev(30.0, 32.5) for _ in range(50)]    # 50 <= n < 120
    level, dtype, realness = gdelt_chokepoint_signal(cp, cluster, radius_km=300.0)
    assert level == "HIGH" and realness == "live"
    assert dtype == "ACTIVE_CONFLICT"


def test_signal_moderate_volume_is_moderate() -> None:
    cp = _Cp(30.0, 32.5)
    cluster = [_ev(30.0, 32.5) for _ in range(15)]    # 15 <= n < 50
    level, dtype, realness = gdelt_chokepoint_signal(cp, cluster, radius_km=300.0)
    assert level == "MODERATE" and dtype == "CONGESTION" and realness == "live"


def test_signal_low_volume_is_low_not_dark() -> None:
    """A few real articles → a real LOW read, not 'dark'."""
    cp = _Cp(30.0, 32.5)
    few = [_ev(30.0, 32.5) for _ in range(3)]         # 1 <= n < 15
    level, dtype, realness = gdelt_chokepoint_signal(cp, few, radius_km=300.0)
    assert realness == "live"
    assert level == "LOW" and dtype == "NONE"


def test_signal_far_events_do_not_escalate() -> None:
    """A big volume on the far side of the planet → dark (coord attribution)."""
    cp = _Cp(30.0, 32.5)
    far = [_ev(-40.0, -120.0) for _ in range(120)]
    assert gdelt_chokepoint_signal(cp, far, radius_km=250.0) == (None, None, "dark")


def test_signal_matches_by_chokepoint_key_when_supplied() -> None:
    """An explicit key counts only that chokepoint's stamped events, ignoring
    a far-coord cluster that shares the country query but a different node."""
    cp = _Cp(30.0, 32.5)
    mine = [_ev(30.0, 32.5, key="suez") for _ in range(120)]
    other = [_ev(12.5, 43.5, key="bab_el_mandeb") for _ in range(120)]
    level, _, realness = gdelt_chokepoint_signal(
        cp, mine + other, chokepoint_key="suez")
    assert level == "CRITICAL" and realness == "live"


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
        {"title": "Red Sea strike", "url": "http://x", "seendate": "20260601"},
        {"title": "Carrier diverts", "url": "http://y", "seendate": "20260601"},
    ]}

    def _get(url, **_k):
        return _Resp(payload)

    events, realness = fetch_chokepoint_events(
        [("suez", _Cp(30.0, 32.5))], http_get=_get, cache_ttl_hours=0.0,
    )
    assert realness == "live"
    assert len(events) == 2
    # Articles inherit the chokepoint coords (country-level attribution).
    assert all(abs(e.lat - 30.0) < 1e-9 for e in events)
    # Tone is NOT available via artlist — always 0.0, never fabricated.
    assert all(e.tone == 0.0 for e in events)
    assert all(e.chokepoint_key == "suez" for e in events)


def test_fetch_uses_valid_country_query() -> None:
    """The fetch must build a VALID sourcecountry query, never the dead geo near:."""
    seen = {}

    def _get(url, **_k):
        seen["url"] = url
        return _Resp(_articles(1))

    fetch_chokepoint_events(
        [("suez", _Cp(30.0, 32.5))], http_get=_get, cache_ttl_hours=0.0,
    )
    assert "sourcecountry" in seen["url"]
    assert re.search(r"near\d+(?:%3A|:)-?\d", seen["url"]) is None


def test_fetch_skips_unmapped_chokepoint() -> None:
    """A chokepoint with no mapped country is SKIPPED — honest no-signal."""
    called = {"n": 0}

    def _get(url, **_k):
        called["n"] += 1
        return _Resp(_articles(5))

    events, realness = fetch_chokepoint_events(
        [("no_such_chokepoint", _Cp(0.0, 0.0))],
        http_get=_get, cache_ttl_hours=0.0,
    )
    assert called["n"] == 0            # never queried
    assert events == [] and realness == "unavailable"


def test_fetch_parses_geojson_payload() -> None:
    """Legacy GeoJSON shape is still parsed harmlessly if ever present."""
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
    assert len(events) == 3
    assert all(abs(e.lon - 32.5) < 1e-9 and abs(e.lat - 30.0) < 1e-9 for e in events)


def test_fetch_real_but_empty_is_empty_realness() -> None:
    """A REAL JSON response with zero articles → 'empty' (not 'unavailable')."""
    def _get(url, **_k):
        return _Resp({"articles": []})

    events, realness = fetch_chokepoint_events(
        [("suez", _Cp(30.0, 32.5))], http_get=_get, cache_ttl_hours=0.0,
    )
    assert events == [] and realness == "empty"


def test_fetch_rate_limit_text_is_unavailable() -> None:
    """A 200 with GDELT's plaintext rate-limit notice is a FAILURE, not empty."""
    def _get(url, **_k):
        return _Resp(None, status_code=200,
                     text="Please limit requests to one every 5 seconds")

    events, realness = fetch_chokepoint_events(
        [("suez", _Cp(30.0, 32.5))], http_get=_get, cache_ttl_hours=0.0,
    )
    assert events == [] and realness == "unavailable"


def test_fetch_invalid_query_plaintext_is_unavailable_not_empty(tmp_path, monkeypatch) -> None:
    """A 200 plain-text error body (e.g. the invalid-NEAR notice) must be
    realness 'unavailable'/'failed' — NEVER 'empty' — and must NOT be cached."""
    import data.gdelt_feed as gf
    # Isolate the cache so we can assert nothing was written for this query.
    monkeypatch.setattr(gf, "_CACHE_DIR", tmp_path)

    def _get(url, **_k):
        return _Resp(None, status_code=200,
                     text="Your query contained an invalid NEAR search. "
                          "Please see the documentation for details.")

    events, realness = fetch_chokepoint_events(
        [("suez", _Cp(30.0, 32.5))], http_get=_get, cache_ttl_hours=0.0,
    )
    assert events == [] and realness == "unavailable"   # NOT "empty"
    # Nothing cached on a structural failure.
    assert list(tmp_path.glob("*.json")) == []


def test_fetch_non_200_is_unavailable() -> None:
    def _get(url, **_k):
        return _Resp({"articles": [{"title": "x"}]}, status_code=404)

    events, realness = fetch_chokepoint_events(
        [("suez", _Cp(30.0, 32.5))], http_get=_get, cache_ttl_hours=0.0,
    )
    assert events == [] and realness == "unavailable"
