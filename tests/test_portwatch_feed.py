"""Offline-safe contract tests for data/portwatch_feed.py.

No network: an injected ``http_get`` returns canned ArcGIS payloads. The
load-bearing properties are (a) a real fetch parses to basis='real', (b) any
failure (network/non-200/non-JSON/ArcGIS-error) is 'unavailable' with ZERO rows
(never a fabricated signal, never cached), and (c) a genuine zero-row fetch is
'empty', distinct from 'unavailable'.
"""
from __future__ import annotations

import json

import pytest

from data import portwatch_feed as pw
from data.portwatch_feed import (
    fetch_chokepoint_transits,
    latest_transits,
    transit_drop_ratio,
    ChokepointTransit,
)


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    # Redirect the JSON cache sidecar to a temp dir so tests never read/write
    # the real cache/portwatch/.
    monkeypatch.setattr(pw, "_CACHE_DIR", tmp_path / "pw")


class _Resp:
    def __init__(self, payload=None, *, status=200, text=None):
        self._payload = payload
        self.status_code = status
        self.text = text if text is not None else (
            json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def _feature(cid, name, date, n_total, capacity=1.0, **extra):
    return {"attributes": {"portid": cid, "portname": name, "date": date,
                           "n_total": n_total, "capacity": capacity, **extra}}


def _getter(resp_or_exc):
    def g(url, params=None, timeout=None, **kw):
        if isinstance(resp_or_exc, Exception):
            raise resp_or_exc
        return resp_or_exc
    return g


# ── basis="real" ─────────────────────────────────────────────────────────────

def test_real_fetch_parses_to_real_basis():
    payload = {"features": [
        _feature("chokepoint1", "Suez Canal", "2026-06-14", 39, capacity=1.4e6,
                 n_container=8, n_tanker=12),
        _feature("chokepoint2", "Panama Canal", "2026-06-13", 30, capacity=9.0e5),
    ]}
    res = fetch_chokepoint_transits(http_get=_getter(_Resp(payload)),
                                    cache_ttl_hours=6.0)
    assert res.basis == "real" and res.is_real
    assert res.latest_date == "2026-06-14"
    assert len(res.rows) == 2
    suez = next(r for r in res.rows if r.chokepoint_id == "chokepoint1")
    assert suez.n_total == 39 and suez.capacity == pytest.approx(1.4e6)
    assert suez.n_container == 8 and suez.name == "Suez Canal"


# ── never-downgrade: failures are 'unavailable' with ZERO rows ───────────────

def test_network_exception_is_unavailable_not_raising():
    res = fetch_chokepoint_transits(http_get=_getter(RuntimeError("net down")),
                                    cache_ttl_hours=6.0)
    assert res.basis == "unavailable" and res.rows == [] and not res.is_real


def test_non_200_is_unavailable():
    res = fetch_chokepoint_transits(http_get=_getter(_Resp({}, status=503)),
                                    cache_ttl_hours=6.0)
    assert res.basis == "unavailable" and res.rows == []


def test_non_json_200_body_is_failure_not_empty_and_not_cached():
    res = fetch_chokepoint_transits(
        http_get=_getter(_Resp(text="Please limit your requests", status=200)),
        cache_ttl_hours=6.0)
    assert res.basis == "unavailable"            # NOT "empty"
    # A structural failure must not have written a cache sidecar.
    assert not (pw._CACHE_DIR).exists() or not any(pw._CACHE_DIR.glob("*.json"))


def test_arcgis_error_payload_is_unavailable():
    payload = {"error": {"code": 400, "message": "Invalid query"}}
    res = fetch_chokepoint_transits(http_get=_getter(_Resp(payload)),
                                    cache_ttl_hours=6.0)
    assert res.basis == "unavailable" and res.rows == []


# ── genuine zero-row fetch is 'empty', distinct from 'unavailable' ───────────

def test_real_fetch_zero_rows_is_empty():
    res = fetch_chokepoint_transits(http_get=_getter(_Resp({"features": []})),
                                    cache_ttl_hours=6.0)
    assert res.basis == "empty" and res.rows == []


def test_rows_with_no_id_or_date_are_dropped_not_guessed():
    payload = {"features": [
        _feature("", "Nowhere", "2026-06-14", 10),          # no id
        {"attributes": {"portid": "chokepoint9", "n_total": 5}},  # no date
        _feature("chokepoint1", "Suez Canal", "2026-06-14", 39),
    ]}
    res = fetch_chokepoint_transits(http_get=_getter(_Resp(payload)),
                                    cache_ttl_hours=6.0)
    assert [r.chokepoint_id for r in res.rows] == ["chokepoint1"]


# ── cache: a fresh sidecar serves without re-hitting the network ─────────────

def test_cache_hit_serves_real_without_network():
    payload = {"features": [_feature("chokepoint1", "Suez Canal", "2026-06-14", 39)]}
    first = fetch_chokepoint_transits(http_get=_getter(_Resp(payload)),
                                      cache_ttl_hours=6.0)
    assert first.basis == "real"
    # Second call: getter RAISES, but a fresh cache must serve the real rows
    # (never downgrade to unavailable when we have fresh cached truth).
    second = fetch_chokepoint_transits(http_get=_getter(RuntimeError("net down")),
                                       cache_ttl_hours=6.0)
    assert second.basis == "real" and len(second.rows) == 1


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_latest_transits_picks_most_recent_per_chokepoint():
    rows = [
        ChokepointTransit("c1", "Suez", "2026-06-10", 50, 1.0),
        ChokepointTransit("c1", "Suez", "2026-06-14", 39, 1.0),   # newer
        ChokepointTransit("c2", "Panama", "2026-06-13", 30, 1.0),
    ]
    assert latest_transits(rows) == {"c1": 39, "c2": 30}


def test_transit_drop_ratio_detects_collapse_and_normal():
    # A collapse: 90 days of ~100 transits, then 7 days of ~10 → ratio → ~0.9.
    collapse = ([ChokepointTransit("c1", "Suez", f"2026-{(i//28)+1:02d}-{(i%28)+1:02d}",
                                   100, 1.0) for i in range(90)]
                + [ChokepointTransit("c1", "Suez", f"2026-05-{i+1:02d}", 10, 1.0)
                   for i in range(7)])
    ratio = transit_drop_ratio(collapse, "c1", recent=7, baseline=90)
    assert ratio is not None and ratio > 0.5

    steady = [ChokepointTransit("c1", "Suez", f"2026-{(i//28)+1:02d}-{(i%28)+1:02d}",
                                100, 1.0) for i in range(100)]
    assert abs(transit_drop_ratio(steady, "c1", recent=7, baseline=90)) < 0.05


def test_transit_drop_ratio_insufficient_history_is_none_not_zero():
    rows = [ChokepointTransit("c1", "Suez", "2026-06-14", 39, 1.0)]
    assert transit_drop_ratio(rows, "c1") is None
