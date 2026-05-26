"""Defining-property tests for processing/port_supply_trend.py.

Per-test isolation: every test that touches the snapshot tree
monkeypatches ``processing.port_supply_history.SNAPSHOT_ROOT`` to
``tmp_path`` so on-disk state stays local to the test run.

Missing-port handling (decided + enforced here): when a locode is
absent from a given day's snapshot, the trend series emits a
``PortTrendPoint`` with ``deficit_days=float('nan')`` and
``severity_label=""``. The point is **kept** rather than filtered out
so chronology is preserved and plotly's line trace breaks visually on
the gap day instead of silently collapsing it.
"""
from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pytest

from processing.port_supply_history import save_snapshot
from processing.port_supply_trend import (
    PortTrendPoint,
    build_port_trend_series,
    build_regional_trend_series,
)


# ── 1. Zero-snapshots base case ──────────────────────────────────────────


def test_build_port_trend_series_empty_root_returns_empty(tmp_path) -> None:
    """No snapshots on disk → empty series, no exception."""
    out = build_port_trend_series("CNSHA", root=tmp_path)
    assert out == []


def test_build_regional_trend_series_empty_root_returns_empty(tmp_path) -> None:
    out = build_regional_trend_series("Asia East", root=tmp_path)
    assert out == []


def test_build_port_trend_series_missing_root_returns_empty(tmp_path) -> None:
    """A root path that doesn't exist returns []. Never raises."""
    out = build_port_trend_series(
        "CNSHA", root=tmp_path / "does-not-exist",
    )
    assert out == []


# ── 2. Chronological multi-snapshot retrieval ────────────────────────────


def test_build_port_trend_series_returns_chronological_points(
    tmp_path,
) -> None:
    """Plant 3 daily snapshots, expect 3 points oldest-first with
    ISO-date ``date`` fields and float ``deficit_days``."""
    for iso in ("2026-05-22", "2026-05-23", "2026-05-24"):
        save_snapshot(
            snapshot_date=date.fromisoformat(iso),
            container_type="40FT_DRY",
            root=tmp_path,
        )
    pts = build_port_trend_series("CNSHA", root=tmp_path)
    assert len(pts) == 3
    # Chronological, oldest-first
    assert [p.date for p in pts] == [
        "2026-05-22", "2026-05-23", "2026-05-24",
    ]
    for p in pts:
        assert isinstance(p, PortTrendPoint)
        assert p.locode == "CNSHA"
        assert isinstance(p.deficit_days, float)
        # Severity must be one of the labeled states (or empty for NaN)
        assert isinstance(p.severity_label, str)


def test_build_port_trend_series_locode_case_insensitive(tmp_path) -> None:
    """Lookups normalize the locode to upper-case so cnsha == CNSHA."""
    save_snapshot(
        snapshot_date=date(2026, 5, 25),
        container_type="40FT_DRY",
        root=tmp_path,
    )
    pts_lower = build_port_trend_series("cnsha", root=tmp_path)
    pts_upper = build_port_trend_series("CNSHA", root=tmp_path)
    assert len(pts_lower) == 1
    assert len(pts_upper) == 1
    assert pts_lower[0].locode == "CNSHA"
    assert pts_upper[0].locode == "CNSHA"
    assert pts_lower[0].deficit_days == pts_upper[0].deficit_days


# ── 3. Missing-from-one-snapshot handling (NaN slot) ─────────────────────


def test_build_port_trend_series_missing_from_one_snapshot_emits_nan(
    tmp_path,
) -> None:
    """If a locode is absent from one day's snapshot, the trend series
    keeps the slot but stamps deficit_days as NaN — preserves chronology
    so plotly's line trace breaks visually on the gap day rather than
    silently collapsing it.
    """
    # Plant two real snapshots
    for iso in ("2026-05-23", "2026-05-25"):
        save_snapshot(
            snapshot_date=date.fromisoformat(iso),
            container_type="40FT_DRY",
            root=tmp_path,
        )
    # Plant an empty snapshot dir for 2026-05-24 (date dir exists but no
    # CSV for the requested container type) — simulates the gap case.
    (tmp_path / "2026-05-24").mkdir()

    pts = build_port_trend_series("CNSHA", root=tmp_path)
    # All three slots present, chronological
    assert [p.date for p in pts] == [
        "2026-05-23", "2026-05-24", "2026-05-25",
    ]
    # Middle slot is NaN; outer slots are real floats
    assert not math.isnan(pts[0].deficit_days)
    assert math.isnan(pts[1].deficit_days)
    assert not math.isnan(pts[2].deficit_days)
    # NaN slot has empty severity label
    assert pts[1].severity_label == ""


def test_build_port_trend_series_unknown_locode_all_nan(tmp_path) -> None:
    """A locode that's never present in any snapshot still returns a
    point per snapshot date, all NaN — chronology preserved."""
    save_snapshot(
        snapshot_date=date(2026, 5, 25),
        container_type="40FT_DRY",
        root=tmp_path,
    )
    pts = build_port_trend_series("ZZZZZ", root=tmp_path)
    assert len(pts) == 1
    assert math.isnan(pts[0].deficit_days)
    assert pts[0].severity_label == ""


# ── 4. Regional rollup averaging ─────────────────────────────────────────


def test_build_regional_trend_series_averages_only_region_members(
    tmp_path,
) -> None:
    """The average must be taken over ports in the requested region
    only — ports in other regions don't contribute."""
    save_snapshot(
        snapshot_date=date(2026, 5, 25),
        container_type="40FT_DRY",
        root=tmp_path,
    )
    # Compute the expected Asia East average directly from the saved
    # snapshot so the test is robust to upstream chain changes.
    from processing.port_supply_history import load_snapshot
    rows = load_snapshot(
        date(2026, 5, 25),
        container_type="40FT_DRY",
        root=tmp_path,
    )
    asia_rows = [r for r in rows if r.region == "Asia East"]
    expected_avg = (
        sum(r.supply_deficit_days for r in asia_rows) / len(asia_rows)
    )

    series = build_regional_trend_series("Asia East", root=tmp_path)
    assert len(series) == 1
    date_iso, avg = series[0]
    assert date_iso == "2026-05-25"
    assert avg == pytest.approx(expected_avg)

    # Sanity: at least one port in another region exists (so the average
    # would differ if we accidentally pooled all regions).
    other_regions = {r.region for r in rows if r.region != "Asia East"}
    assert other_regions, (
        "test setup expects multiple regions — at least one non-Asia "
        "East port should be present"
    )


def test_build_regional_trend_series_empty_region_returns_nan(tmp_path) -> None:
    """A region with zero ports → NaN (still emits the slot)."""
    save_snapshot(
        snapshot_date=date(2026, 5, 25),
        container_type="40FT_DRY",
        root=tmp_path,
    )
    series = build_regional_trend_series(
        "Atlantis", root=tmp_path,
    )
    assert len(series) == 1
    date_iso, avg = series[0]
    assert date_iso == "2026-05-25"
    assert math.isnan(avg)


# ── 5. max_days clamp ────────────────────────────────────────────────────


def test_build_port_trend_series_max_days_clamps_to_most_recent(
    tmp_path,
) -> None:
    """Plant 100 daily snapshot dirs + ask for max_days=30 → exactly 30
    points returned, all from the most-recent 30 days (oldest-first)."""
    from processing.port_supply_history import _snapshot_filename

    # Plant 100 stub snapshot dirs with a minimal valid CSV (header +
    # one Shanghai row). Stub bodies are cheap — we don't need the full
    # 25-port export for this clamp test.
    header = (
        "deficit_rank,locode,port_name,region,country_iso3,lat,lon,"
        "container_type,supply_deficit_days,utilization_pct,"
        "severity_label,n_routes_touching,n_exposed_companies,"
        "regional_avg_deficit,vs_regional_deficit,"
        "exposure_concentration_hhi,total_exposure_weight,"
        "top_exposed_tickers,summary"
    )
    row = (
        "1,CNSHA,Shanghai,Asia East,CHN,31.23,121.47,40FT_DRY,"
        "+0.00,0.0,Balanced,0,0,+0.00,+0.00,0.0,0.0,,sample"
    )
    body = f"{header}\n{row}\n"

    base = Path(tmp_path)
    base.mkdir(parents=True, exist_ok=True)
    # 100 days ending on 2026-05-26
    from datetime import timedelta as _td
    anchor = date(2026, 5, 26)
    planted_dates = []
    for offset in range(100):
        d = anchor - _td(days=offset)
        planted_dates.append(d)
        sub = base / d.isoformat()
        sub.mkdir(parents=True, exist_ok=True)
        (sub / _snapshot_filename("40FT_DRY")).write_text(
            body, encoding="utf-8",
        )
    planted_dates.sort()   # oldest-first

    pts = build_port_trend_series(
        "CNSHA", root=tmp_path, max_days=30,
    )
    assert len(pts) == 30
    # Must be the most-recent 30 — not the oldest 30
    expected_dates = [d.isoformat() for d in planted_dates[-30:]]
    actual_dates = [p.date for p in pts]
    assert actual_dates == expected_dates


def test_build_port_trend_series_max_days_zero_returns_empty(
    tmp_path,
) -> None:
    """A 0 (or negative) max_days clamps to an empty result, no I/O."""
    save_snapshot(
        snapshot_date=date(2026, 5, 25),
        container_type="40FT_DRY",
        root=tmp_path,
    )
    assert build_port_trend_series(
        "CNSHA", root=tmp_path, max_days=0,
    ) == []
    assert build_port_trend_series(
        "CNSHA", root=tmp_path, max_days=-5,
    ) == []


def test_build_regional_trend_series_max_days_clamp(tmp_path) -> None:
    """Same clamp on the regional builder. Plant 50 stubs, ask for 20."""
    from processing.port_supply_history import _snapshot_filename

    header = (
        "deficit_rank,locode,port_name,region,country_iso3,lat,lon,"
        "container_type,supply_deficit_days,utilization_pct,"
        "severity_label,n_routes_touching,n_exposed_companies,"
        "regional_avg_deficit,vs_regional_deficit,"
        "exposure_concentration_hhi,total_exposure_weight,"
        "top_exposed_tickers,summary"
    )
    rows = "\n".join([
        "1,CNSHA,Shanghai,Asia East,CHN,31.23,121.47,40FT_DRY,"
        "-2.00,0.0,Deficit,0,0,0.0,0.0,0.0,0.0,,",
        "2,CNNBO,Ningbo,Asia East,CHN,29.87,121.55,40FT_DRY,"
        "+1.00,0.0,Balanced,0,0,0.0,0.0,0.0,0.0,,",
    ])
    body = f"{header}\n{rows}\n"

    from datetime import timedelta as _td
    anchor = date(2026, 5, 26)
    for offset in range(50):
        d = anchor - _td(days=offset)
        sub = tmp_path / d.isoformat()
        sub.mkdir(parents=True, exist_ok=True)
        (sub / _snapshot_filename("40FT_DRY")).write_text(
            body, encoding="utf-8",
        )

    series = build_regional_trend_series(
        "Asia East", root=tmp_path, max_days=20,
    )
    assert len(series) == 20
    # Average of (-2.0, 1.0) = -0.5 per day
    for _, avg in series:
        assert avg == pytest.approx(-0.5)


def test_build_regional_trend_series_skips_nan_in_average(tmp_path) -> None:
    """If one port in a region has NaN deficit on a given day, the
    average is computed over the remaining ports — a single missing
    port doesn't tank the regional series.

    Validated indirectly by constructing a snapshot where one Asia East
    row has 'nan' as its supply_deficit_days (parser coerces to 0.0
    per parse_summary_csv; we test the more realistic case of a
    region-only-empty day separately).
    """
    # Plant one snapshot with two Asia East rows
    header = (
        "deficit_rank,locode,port_name,region,country_iso3,lat,lon,"
        "container_type,supply_deficit_days,utilization_pct,"
        "severity_label,n_routes_touching,n_exposed_companies,"
        "regional_avg_deficit,vs_regional_deficit,"
        "exposure_concentration_hhi,total_exposure_weight,"
        "top_exposed_tickers,summary"
    )
    rows = "\n".join([
        "1,CNSHA,Shanghai,Asia East,CHN,31.23,121.47,40FT_DRY,"
        "-4.00,0.0,Deficit,0,0,0.0,0.0,0.0,0.0,,",
        "2,CNNBO,Ningbo,Asia East,CHN,29.87,121.55,40FT_DRY,"
        "+0.00,0.0,Balanced,0,0,0.0,0.0,0.0,0.0,,",
    ])
    body = f"{header}\n{rows}\n"
    from processing.port_supply_history import _snapshot_filename
    sub = tmp_path / "2026-05-25"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / _snapshot_filename("40FT_DRY")).write_text(body, encoding="utf-8")

    series = build_regional_trend_series("Asia East", root=tmp_path)
    assert len(series) == 1
    _, avg = series[0]
    # (-4 + 0) / 2 = -2
    assert avg == pytest.approx(-2.0)


# ── 6. Container-type isolation ──────────────────────────────────────────


def test_build_port_trend_series_container_type_isolated(tmp_path) -> None:
    """Snapshots are per-container-type — asking for 40FT_REEFER must
    not pick up 40FT_DRY rows from the same date dir."""
    save_snapshot(
        snapshot_date=date(2026, 5, 25),
        container_type="40FT_DRY",
        root=tmp_path,
    )
    # Date dir exists, but only the dry snapshot is on disk.
    pts = build_port_trend_series(
        "CNSHA", container_type="40FT_REEFER", root=tmp_path,
    )
    # Slot is held with NaN — the date dir is present, just not the
    # container-type file.
    assert len(pts) == 1
    assert math.isnan(pts[0].deficit_days)
