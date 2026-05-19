"""Tests for the synthetic fallback feeds.

The Ship Tracker runs its synthetic fallbacks constantly (live AIS / trade
APIs are rate-limited), so their *realism contract* matters as much as a
live feed would. These tests pin three things for the synthetic paths in
``data/ais_feed.py``, ``data/aisstream_feed.py`` and ``data/comtrade_feed.py``:

* the output **schema** (DataFrame columns / vessel-dict keys) is stable,
* deterministic paths stay **deterministic**, and
* the improved value-realism behaviours actually hold — smooth daily
  congestion variation, route-biased destinations, ETA clustering,
  congestion-aware anchored fraction, and per-country trade sector mixes.

No Streamlit runtime and no live network access is required.
"""
from __future__ import annotations

import datetime as _dt
import random
from collections import Counter

import pytest

from data.ais_feed import _PORT_VESSEL_BASELINES, _synthetic_congestion
from data.aisstream_feed import (
    _destination_for_port,
    _port_congestion,
    _synthetic_vessels_for_port,
    _synth_vessel,
)
from data.comtrade_feed import _CATEGORY_SHARES, _category_shares_for_country

_AIS_COLS = ["date", "port_locode", "vessel_count", "vessel_type", "source"]
_VESSEL_KEYS = {
    "mmsi", "name", "vessel_type", "vessel_type_code", "lat", "lon",
    "speed_kts", "heading", "destination", "eta", "length_m", "flag",
    "last_updated", "color", "source",
}


# ── ais_feed: synthetic congestion ───────────────────────────────────────────

def test_synthetic_congestion_schema_and_determinism():
    df = _synthetic_congestion("CNSHA")
    assert list(df.columns) == _AIS_COLS
    assert len(df) == 1
    assert df["port_locode"].iloc[0] == "CNSHA"
    assert df["vessel_type"].iloc[0] == "cargo"
    assert int(df["vessel_count"].iloc[0]) > 0
    # Deterministic within a day (seeded by port + date).
    again = _synthetic_congestion("CNSHA")
    assert int(again["vessel_count"].iloc[0]) == int(df["vessel_count"].iloc[0])


def test_synthetic_congestion_count_near_baseline():
    """The synthetic count should stay within a sane band of the port's
    known baseline — not collapse to zero, not blow up."""
    for locode in ("CNSHA", "SGSIN", "USLAX", "NLRTM", "BRSAO"):
        count = int(_synthetic_congestion(locode)["vessel_count"].iloc[0])
        baseline = _PORT_VESSEL_BASELINES[locode]
        assert 0.5 * baseline <= count <= 1.7 * baseline


def test_synthetic_congestion_smooth_daily_variation():
    """Day-to-day congestion must drift smoothly — no week-boundary steps.

    We patch the module clock across a month of consecutive days and assert
    no single-day jump exceeds a small fraction of the port baseline.
    """
    import unittest.mock as mock

    import data.ais_feed as af

    for locode, baseline in (("CNSHA", 180), ("USLAX", 60)):
        counts = []
        for day in range(1, 29):
            with mock.patch("data.ais_feed.datetime") as m:
                m.now.return_value = _dt.datetime(2026, 6, day, 12, 0, 0)
                m.side_effect = lambda *a, **k: _dt.datetime(*a, **k)
                counts.append(int(af._synthetic_congestion(locode)["vessel_count"].iloc[0]))
        max_jump = max(abs(counts[i + 1] - counts[i]) for i in range(len(counts) - 1))
        # A smooth feed never jumps more than ~8% of baseline in one day.
        assert max_jump <= 0.08 * baseline, (locode, max_jump, counts)


# ── aisstream: synthetic vessels ─────────────────────────────────────────────

def test_synthetic_vessels_schema_and_determinism():
    vessels = _synthetic_vessels_for_port("CNSHA", 31.23, 121.47)
    assert vessels, "expected at least one synthetic vessel"
    for v in vessels:
        assert set(v.keys()) == _VESSEL_KEYS
        assert isinstance(v["mmsi"], str)
        assert isinstance(v["speed_kts"], float)
        assert isinstance(v["heading"], int)
        assert isinstance(v["eta"], str) and v["eta"]
    # Cached / deterministic within the synthetic TTL window.
    import data.aisstream_feed as af

    af._SYNTH_CACHE.clear()
    first = af._synthetic_vessels_for_port("USLAX", 33.74, -118.27)
    second = af._synthetic_vessels_for_port("USLAX", 33.74, -118.27)
    assert first == second


def test_destination_is_route_biased():
    """A vessel near a port should mostly head to destinations served by a
    real lane out of that port, with a minority heading elsewhere."""
    lanes = {"USLAX", "NLRTM", "SGSIN", "JPYOK", "BRSAO"}  # CNSHA lanes
    counts = Counter(
        _destination_for_port("CNSHA", random.Random(i)) for i in range(4000)
    )
    lane_share = sum(counts[d] for d in lanes) / sum(counts.values())
    assert 0.6 < lane_share < 0.95, lane_share
    # A vessel never lists its own port as destination.
    assert "CNSHA" not in counts
    # Off-lane destinations remain reachable.
    assert sum(counts.values()) - sum(counts[d] for d in lanes) > 0


def test_destination_unknown_origin_falls_back_uniform():
    """An origin with no registered lanes must not crash and should spread
    across the destination pool."""
    counts = Counter(
        _destination_for_port("route_unknown", random.Random(i)) for i in range(600)
    )
    assert len(counts) >= 6


def test_anchored_fraction_rises_with_congestion():
    """The share of anchored (speed <= 1 kt) vessels should increase with
    the origin port's congestion level."""
    def anchored_fraction(locode: str, lat: float, lon: float) -> float:
        cong = _port_congestion(locode)
        n = 4000
        anchored = sum(
            1
            for i in range(n)
            if _synth_vessel(
                100_000_000 + i * 131, 0, lat, lon, 0.4,
                origin=locode, congestion=cong,
            )["speed_kts"] <= 1.0
        )
        return anchored / n

    busy = anchored_fraction("CNSHA", 31.23, 121.47)   # high congestion
    quiet = anchored_fraction("GRPIR", 37.94, 23.64)   # low congestion
    assert busy > quiet
    # Both fractions stay in a believable range.
    assert 0.10 < quiet < 0.55 and 0.10 < busy < 0.60
    assert _port_congestion("CNSHA") > _port_congestion("GRPIR")


def test_eta_clusters_and_stays_bounded():
    """ETAs should cluster around realistic transit times rather than the
    old flat 12-240h uniform spread, and stay within sane bounds."""
    etas = []
    for i in range(3000):
        v = _synth_vessel(
            300_000_000 + i * 97, 0, 31.23, 121.47, 0.4,
            origin="CNSHA", congestion=0.5,
        )
        eta_dt = _dt.datetime.strptime(v["eta"], "%Y-%m-%d %H:%M UTC")
        hours = (eta_dt - _dt.datetime.utcnow()).total_seconds() / 3600
        etas.append(hours)
    # Bounded (allow ~1h slack for minute-truncation in the string format).
    assert all(5.0 <= h <= 361.0 for h in etas), (min(etas), max(etas))
    # Clustered: CNSHA serves short lanes (Japan 3d, Singapore 5d), so a
    # clear share of ETAs sits well below the old uniform midpoint.
    short_share = sum(1 for h in etas if h < 120) / len(etas)
    assert short_share > 0.35


# ── comtrade: per-country sector shares ──────────────────────────────────────

def test_country_category_shares_normalised():
    """Every per-country sector-share table is normalised to sum to 1.0 and
    keeps the canonical category set."""
    for iso2 in ("CN", "US", "DE", "JP", "KR", "BR", "LK", "NL"):
        shares = _category_shares_for_country(iso2)
        assert set(shares) == set(_CATEGORY_SHARES)
        assert abs(sum(shares.values()) - 1.0) < 1e-9


def test_unknown_country_uses_global_shares():
    """A country with no hand-tuned table falls back to the global shares."""
    fallback = _category_shares_for_country("ZZ")
    total = sum(_CATEGORY_SHARES.values())
    expected = {k: v / total for k, v in _CATEGORY_SHARES.items()}
    for cat, val in fallback.items():
        assert abs(val - expected[cat]) < 1e-9


def test_country_export_mixes_differ():
    """Different exporters must get genuinely different sector mixes — the
    whole point of the per-country table."""
    cn = _category_shares_for_country("CN")
    de = _category_shares_for_country("DE")
    br = _category_shares_for_country("BR")
    # Germany is automotive-heavy; Brazil is agriculture/metals-heavy;
    # China leads in electronics.
    assert de["automotive"] > cn["automotive"]
    assert br["agriculture"] > cn["agriculture"]
    assert cn["electronics"] > br["electronics"]


def test_wb_merchandise_fallback_schema():
    """The World Bank merchandise fallback must keep its exact DataFrame
    schema (columns, row count, flow / source values)."""
    from data.cache_manager import CacheManager
    from data.comtrade_feed import _wb_merchandise_fallback

    results = _wb_merchandise_fallback(CacheManager(), ttl_hours=168.0)
    assert results, "fallback produced no ports"
    expected_cols = [
        "date", "port_locode", "hs_code", "flow",
        "value_usd", "net_weight_kg", "country_iso2", "source",
    ]
    for locode, df in results.items():
        assert list(df.columns) == expected_cols
        # 7 categories x 2 flows.
        assert len(df) == 14
        assert set(df["flow"]) == {"X", "M"}
        assert (df["source"] == "wb_synthetic").all()
        assert df["port_locode"].iloc[0] == locode
        assert (df["value_usd"] > 0).all()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
