"""Defining-property tests for processing/snapshot_integrity.py."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from processing.snapshot_integrity import (
    SnapshotIntegrityReport,
    check_all_snapshots,
    check_snapshot_integrity,
    expected_filenames,
    summarize_integrity_run,
)
from processing.port_supply_history import (
    save_regional_snapshot,
    save_snapshot,
    snapshot_dir_for,
)


# ── 1. Expected-filenames lookup ─────────────────────────────────────────


def test_expected_filenames_contains_per_port_and_regional() -> None:
    files = expected_filenames("40FT_DRY")
    assert any("port_supply_summary_40ft_dry" in f for f in files)
    assert any("port_supply_regional_rollup_40ft_dry" in f for f in files)


def test_expected_filenames_lowercases_container_type() -> None:
    """Container slug is lowercase in filenames — the pipeline's contract."""
    files = expected_filenames("40FT_REEFER")
    for f in files:
        assert "40ft_reefer" in f
        assert "40FT_REEFER" not in f


# ── 2. Per-date integrity check ──────────────────────────────────────────


def test_healthy_snapshot_dir_reports_ok(tmp_path) -> None:
    """Both per-port + regional present → ok=True, no missing or corrupted."""
    save_snapshot(snapshot_date=date(2026, 5, 26), root=tmp_path)
    save_regional_snapshot(snapshot_date=date(2026, 5, 26), root=tmp_path)
    r = check_snapshot_integrity(date(2026, 5, 26), root=tmp_path)
    assert r.ok is True
    assert r.files_missing == []
    assert r.files_corrupted == []


def test_missing_per_port_file_flagged(tmp_path) -> None:
    """Only regional present → per-port summary lands in files_missing."""
    save_regional_snapshot(snapshot_date=date(2026, 5, 26), root=tmp_path)
    r = check_snapshot_integrity(date(2026, 5, 26), root=tmp_path)
    assert r.ok is False
    assert any("port_supply_summary" in f for f in r.files_missing)


def test_missing_regional_file_flagged(tmp_path) -> None:
    save_snapshot(snapshot_date=date(2026, 5, 26), root=tmp_path)
    r = check_snapshot_integrity(date(2026, 5, 26), root=tmp_path)
    assert r.ok is False
    assert any("regional_rollup" in f for f in r.files_missing)


def test_missing_entire_dir_lists_all_files_as_missing(tmp_path) -> None:
    r = check_snapshot_integrity(date(2026, 5, 26), root=tmp_path)
    assert r.ok is False
    assert len(r.files_missing) == len(r.files_expected)
    assert r.files_present == []


def test_corrupted_per_port_csv_lands_in_corrupted_list(tmp_path) -> None:
    """A truncated per-port CSV (no header, partial data) → corrupted flag."""
    save_snapshot(snapshot_date=date(2026, 5, 26), root=tmp_path)
    save_regional_snapshot(snapshot_date=date(2026, 5, 26), root=tmp_path)
    # Truncate the per-port CSV to one byte — no parseable rows.
    d = snapshot_dir_for(date(2026, 5, 26), root=tmp_path)
    per_port = next(p for p in d.iterdir() if "summary" in p.name)
    per_port.write_bytes(b"\xef")   # leftover BOM, no rows
    r = check_snapshot_integrity(date(2026, 5, 26), root=tmp_path)
    assert r.ok is False
    # Either flagged as corrupted OR a parse warning surfaced
    assert (
        any("summary" in f for f in r.files_corrupted)
        or len(r.parse_warnings) > 0
    )


# ── 3. Bulk sweep + aggregate summary ────────────────────────────────────


def test_check_all_snapshots_runs_one_per_date(tmp_path) -> None:
    save_snapshot(snapshot_date=date(2026, 5, 24), root=tmp_path)
    save_regional_snapshot(snapshot_date=date(2026, 5, 24), root=tmp_path)
    save_snapshot(snapshot_date=date(2026, 5, 25), root=tmp_path)
    save_regional_snapshot(snapshot_date=date(2026, 5, 25), root=tmp_path)
    reports = check_all_snapshots(root=tmp_path)
    assert len(reports) == 2


def test_check_all_snapshots_filters_by_since(tmp_path) -> None:
    save_snapshot(snapshot_date=date(2026, 5, 20), root=tmp_path)
    save_regional_snapshot(snapshot_date=date(2026, 5, 20), root=tmp_path)
    save_snapshot(snapshot_date=date(2026, 5, 25), root=tmp_path)
    save_regional_snapshot(snapshot_date=date(2026, 5, 25), root=tmp_path)
    reports = check_all_snapshots(root=tmp_path, since=date(2026, 5, 23))
    assert len(reports) == 1
    assert reports[0].date_iso == "2026-05-25"


def test_check_all_snapshots_empty_root_returns_empty(tmp_path) -> None:
    reports = check_all_snapshots(root=tmp_path / "does-not-exist")
    assert reports == []


def test_summarize_integrity_run_counts_ok_and_problems() -> None:
    reports = [
        SnapshotIntegrityReport(date_iso="2026-05-20", ok=True),
        SnapshotIntegrityReport(
            date_iso="2026-05-21", ok=False,
            files_missing=["x.csv"],
        ),
        SnapshotIntegrityReport(
            date_iso="2026-05-22", ok=False,
            files_corrupted=["y.csv"],
        ),
        SnapshotIntegrityReport(date_iso="2026-05-23", ok=True),
    ]
    s = summarize_integrity_run(reports)
    assert s["n_dates_checked"] == 4
    assert s["n_ok"] == 2
    assert s["n_missing"] == 1
    assert s["n_corrupted"] == 1
    assert s["oldest_problem_date"] == "2026-05-21"


def test_summarize_integrity_run_empty_returns_defensible_dict() -> None:
    s = summarize_integrity_run([])
    assert s["n_dates_checked"] == 0
    assert s["n_ok"] == 0
    assert s["oldest_problem_date"] is None
