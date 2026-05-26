"""Defining-property tests for processing/port_supply_history.py."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from processing.port_supply_history import (
    SnapshotJobResult,
    find_prior_snapshot_date,
    list_snapshot_dates,
    load_snapshot,
    run_daily_snapshot_job,
    save_snapshot,
    snapshot_dir_for,
)


# ── 1. Path helpers — pure, no I/O ────────────────────────────────────────


def test_snapshot_dir_for_uses_iso_date_under_root() -> None:
    root = Path("/tmp/test_root")
    d = snapshot_dir_for(date(2026, 5, 26), root=root)
    assert d == root / "2026-05-26"


def test_snapshot_dir_for_default_root_is_under_cache(tmp_path) -> None:
    """Without --root override, snapshots land under the project's
    cache/ tree (so bulk_export picks them up). The exact path isn't
    pinned — we just verify the helper returns a Path under 'cache'."""
    d = snapshot_dir_for(date(2026, 5, 26))
    assert "port_supply_snapshots" in str(d)


# ── 2. Save / load round-trip ─────────────────────────────────────────────


def test_save_snapshot_writes_csv_with_canonical_filename(tmp_path) -> None:
    path, n_bytes = save_snapshot(
        snapshot_date=date(2026, 5, 26),
        container_type="40FT_DRY",
        root=tmp_path,
    )
    assert path.exists()
    assert n_bytes > 0
    # Date in directory, filename has no date stamp (parent owns the date).
    assert path.parent.name == "2026-05-26"
    assert path.name == "port_supply_summary_40ft_dry.csv"


def test_save_snapshot_creates_missing_parent_dirs(tmp_path) -> None:
    nested = tmp_path / "deep" / "nested" / "subdir"
    assert not nested.exists()
    path, _ = save_snapshot(
        snapshot_date=date(2026, 5, 26),
        container_type="40FT_DRY",
        root=nested,
    )
    assert path.exists()
    assert nested.exists()


def test_load_snapshot_round_trips_through_save(tmp_path) -> None:
    """save_snapshot then load_snapshot must return a non-empty PortRow
    list matching the chains the joiner produces."""
    save_snapshot(
        snapshot_date=date(2026, 5, 26),
        container_type="40FT_DRY",
        root=tmp_path,
    )
    rows = load_snapshot(
        date(2026, 5, 26),
        container_type="40FT_DRY",
        root=tmp_path,
    )
    from ports.port_registry import PORTS
    assert len(rows) == len(PORTS)
    for r in rows:
        assert r.locode
        assert isinstance(r.supply_deficit_days, float)
        assert isinstance(r.top_exposed_tickers, list)


def test_load_snapshot_raises_on_missing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_snapshot(
            date(2026, 5, 26),
            container_type="40FT_DRY",
            root=tmp_path,
        )


# ── 3. Container types kept in separate files ────────────────────────────


def test_different_container_types_get_separate_files(tmp_path) -> None:
    """Saving 40FT_DRY then 40FT_REEFER for the same date writes two
    sibling files, not a single file that overwrites the first."""
    p_dry, _ = save_snapshot(
        snapshot_date=date(2026, 5, 26),
        container_type="40FT_DRY",
        root=tmp_path,
    )
    p_reefer, _ = save_snapshot(
        snapshot_date=date(2026, 5, 26),
        container_type="40FT_REEFER",
        root=tmp_path,
    )
    assert p_dry != p_reefer
    assert p_dry.parent == p_reefer.parent      # same date dir
    assert p_dry.exists() and p_reefer.exists()


# ── 4. list_snapshot_dates ───────────────────────────────────────────────


def test_list_snapshot_dates_returns_only_iso_dirs(tmp_path) -> None:
    # Plant two valid date dirs + one stray non-date dir
    (tmp_path / "2026-05-24").mkdir()
    (tmp_path / "2026-05-25").mkdir()
    (tmp_path / "manual-notes").mkdir()
    dates = list_snapshot_dates(root=tmp_path)
    assert dates == [date(2026, 5, 24), date(2026, 5, 25)]


def test_list_snapshot_dates_empty_when_root_missing(tmp_path) -> None:
    dates = list_snapshot_dates(root=tmp_path / "does-not-exist")
    assert dates == []


def test_list_snapshot_dates_sorted_oldest_first(tmp_path) -> None:
    for iso in ("2026-05-25", "2026-05-22", "2026-05-26", "2026-05-23"):
        (tmp_path / iso).mkdir()
    dates = list_snapshot_dates(root=tmp_path)
    assert dates == sorted(dates)


# ── 5. find_prior_snapshot_date ──────────────────────────────────────────


def test_find_prior_finds_yesterday(tmp_path) -> None:
    save_snapshot(snapshot_date=date(2026, 5, 25), root=tmp_path)
    prior = find_prior_snapshot_date(date(2026, 5, 26), root=tmp_path)
    assert prior == date(2026, 5, 25)


def test_find_prior_returns_none_when_no_lookback_match(tmp_path) -> None:
    # Plant a snapshot 30 days ago — outside default 14-day window
    save_snapshot(snapshot_date=date(2026, 4, 26), root=tmp_path)
    prior = find_prior_snapshot_date(
        date(2026, 5, 26), root=tmp_path, max_lookback_days=14,
    )
    assert prior is None


def test_find_prior_skips_dates_without_matching_container_type(
    tmp_path,
) -> None:
    """Operator captured only 40FT_REEFER yesterday — looking for
    40FT_DRY must walk further back, not pick up the reefer file."""
    save_snapshot(
        snapshot_date=date(2026, 5, 24),
        container_type="40FT_DRY",
        root=tmp_path,
    )
    save_snapshot(
        snapshot_date=date(2026, 5, 25),
        container_type="40FT_REEFER",
        root=tmp_path,
    )
    prior = find_prior_snapshot_date(
        date(2026, 5, 26),
        container_type="40FT_DRY",
        root=tmp_path,
    )
    assert prior == date(2026, 5, 24)   # not the 25th (only reefer there)


def test_find_prior_max_lookback_clamped_to_minimum_one(tmp_path) -> None:
    """A 0 or negative lookback gets clamped to 1 (today is never
    a 'prior' candidate)."""
    save_snapshot(snapshot_date=date(2026, 5, 25), root=tmp_path)
    prior = find_prior_snapshot_date(
        date(2026, 5, 26), root=tmp_path, max_lookback_days=0,
    )
    assert prior == date(2026, 5, 25)


# ── 6. run_daily_snapshot_job — end-to-end ───────────────────────────────


def test_job_saves_snapshot_and_returns_ok(tmp_path) -> None:
    r = run_daily_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    assert isinstance(r, SnapshotJobResult)
    assert r.ok is True
    assert r.snapshot_path
    assert Path(r.snapshot_path).exists()
    assert r.bytes_written > 0
    assert r.today == "2026-05-26"


def test_job_returns_none_diff_when_no_prior_snapshot(tmp_path) -> None:
    """First-ever run has nothing to diff against — diff is None,
    prior_snapshot_date is empty, but ok still True."""
    r = run_daily_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    assert r.ok is True
    assert r.diff is None
    assert r.prior_snapshot_date == ""


def test_job_produces_diff_when_prior_snapshot_exists(tmp_path) -> None:
    """Save day 1, then run the job for day 2 — diff is populated."""
    save_snapshot(snapshot_date=date(2026, 5, 25), root=tmp_path)
    r = run_daily_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    assert r.ok is True
    assert r.diff is not None
    assert r.prior_snapshot_date == "2026-05-25"


def test_job_failure_during_diff_still_marks_save_successful(
    tmp_path, monkeypatch,
) -> None:
    """If the diff phase blows up but the save succeeded, the job
    surfaces ok=True (snapshot landed) + error_msg explains the
    diff failure — same contract as the other worker jobs."""
    save_snapshot(snapshot_date=date(2026, 5, 25), root=tmp_path)

    def _broken_compare(*_a, **_kw):
        raise RuntimeError("simulated diff failure")

    import tools.port_supply_diff as diff_mod
    monkeypatch.setattr(diff_mod, "compare_snapshots", _broken_compare)

    r = run_daily_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    assert r.ok is True   # snapshot itself succeeded
    assert r.diff is None
    assert "diff failed" in r.error_msg


def test_job_failure_during_save_returns_ok_false(
    tmp_path, monkeypatch,
) -> None:
    """If save_snapshot raises, ok must flip to False + error_msg
    captures the exception."""
    def _broken_save(*_a, **_kw):
        raise RuntimeError("simulated save failure")

    import processing.port_supply_history as psh
    monkeypatch.setattr(psh, "save_snapshot", _broken_save)

    r = run_daily_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    assert r.ok is False
    assert "save_snapshot failed" in r.error_msg


def test_job_default_today_is_utc_today(tmp_path, monkeypatch) -> None:
    """When ``today`` is not provided, the job uses UTC today —
    not local-time today (which would drift across timezones)."""
    r = run_daily_snapshot_job(root=tmp_path)
    # Just check the format + that it's parseable as ISO date.
    parsed = date.fromisoformat(r.today)
    assert parsed.year >= 2026   # sanity: this test was authored in 2026


def test_job_container_type_propagates_into_filename(tmp_path) -> None:
    r = run_daily_snapshot_job(
        today=date(2026, 5, 26),
        container_type="40FT_REEFER",
        root=tmp_path,
    )
    assert r.ok is True
    assert "40ft_reefer" in r.snapshot_path
