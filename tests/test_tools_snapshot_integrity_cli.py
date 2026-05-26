"""Defining-property tests for tools/snapshot_integrity_cli.py."""
from __future__ import annotations

import json
from datetime import date

import pytest

from tools.snapshot_integrity_cli import main, _build_parser
from processing.port_supply_history import (
    save_regional_snapshot,
    save_snapshot,
)


# ── 1. Parser shape ──────────────────────────────────────────────────────


def test_parser_requires_date_or_all() -> None:
    """One of --date or --all must be supplied (mutually exclusive group)."""
    with pytest.raises(SystemExit):
        _build_parser().parse_args([])


def test_parser_rejects_both_date_and_all() -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--date", "2026-05-26", "--all"])


# ── 2. --date single check ───────────────────────────────────────────────


def test_date_check_on_healthy_snapshot_returns_zero(tmp_path, capsys) -> None:
    save_snapshot(snapshot_date=date(2026, 5, 26), root=tmp_path)
    save_regional_snapshot(snapshot_date=date(2026, 5, 26), root=tmp_path)
    code = main([
        "--date", "2026-05-26",
        "--root", str(tmp_path),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "2026-05-26" in out


def test_date_check_with_bad_date_format_returns_one(tmp_path, capsys) -> None:
    code = main([
        "--date", "not-a-date",
        "--root", str(tmp_path),
    ])
    assert code == 1
    err = capsys.readouterr().err
    assert "bad --date" in err


def test_date_check_on_missing_snapshot_reports_fail(tmp_path, capsys) -> None:
    code = main([
        "--date", "2026-05-26",
        "--root", str(tmp_path),
    ])
    # Without --verify: exit 0 even though FAIL
    assert code == 0
    out = capsys.readouterr().out
    assert "FAIL" in out


# ── 3. --all sweep ───────────────────────────────────────────────────────


def test_all_sweep_runs_one_per_snapshot_date(tmp_path, capsys) -> None:
    for d in (date(2026, 5, 24), date(2026, 5, 25), date(2026, 5, 26)):
        save_snapshot(snapshot_date=d, root=tmp_path)
        save_regional_snapshot(snapshot_date=d, root=tmp_path)
    code = main(["--all", "--root", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    for iso in ("2026-05-24", "2026-05-25", "2026-05-26"):
        assert iso in out


def test_all_sweep_with_since_clamps_dates(tmp_path, capsys) -> None:
    for d in (date(2026, 5, 20), date(2026, 5, 25)):
        save_snapshot(snapshot_date=d, root=tmp_path)
        save_regional_snapshot(snapshot_date=d, root=tmp_path)
    code = main([
        "--all", "--since", "2026-05-23",
        "--root", str(tmp_path),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "2026-05-25" in out
    assert "2026-05-20" not in out


def test_all_sweep_with_bad_since_returns_one(tmp_path, capsys) -> None:
    code = main([
        "--all", "--since", "bogus",
        "--root", str(tmp_path),
    ])
    assert code == 1
    err = capsys.readouterr().err
    assert "bad --since" in err


# ── 4. --verify gate ────────────────────────────────────────────────────


def test_verify_returns_zero_when_all_healthy(tmp_path) -> None:
    save_snapshot(snapshot_date=date(2026, 5, 26), root=tmp_path)
    save_regional_snapshot(snapshot_date=date(2026, 5, 26), root=tmp_path)
    code = main([
        "--all", "--root", str(tmp_path), "--verify",
    ])
    assert code == 0


def test_verify_returns_one_when_any_unhealthy(tmp_path) -> None:
    save_snapshot(snapshot_date=date(2026, 5, 26), root=tmp_path)
    # No regional snapshot → unhealthy
    code = main([
        "--all", "--root", str(tmp_path), "--verify",
    ])
    assert code == 1


def test_verify_returns_zero_on_empty_root(tmp_path) -> None:
    """No snapshots = nothing to fail. --verify exits 0."""
    code = main([
        "--all", "--root", str(tmp_path), "--verify",
    ])
    assert code == 0


# ── 5. --format json ────────────────────────────────────────────────────


def test_json_format_emits_valid_json_with_required_keys(
    tmp_path, capsys,
) -> None:
    save_snapshot(snapshot_date=date(2026, 5, 26), root=tmp_path)
    save_regional_snapshot(snapshot_date=date(2026, 5, 26), root=tmp_path)
    code = main([
        "--all", "--root", str(tmp_path), "--format", "json",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "reports" in payload
    assert "summary" in payload
    assert payload["summary"]["n_dates_checked"] == 1
