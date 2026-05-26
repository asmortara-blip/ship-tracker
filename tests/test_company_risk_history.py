"""Defining-property tests for processing/company_risk_history.py."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest

from processing.company_risk_history import (
    CompanyRiskHistoryJobResult,
    company_risk_dir_for,
    detect_band_transitions,
    list_company_risk_dates,
    load_company_risk_for_ticker,
    run_daily_company_risk_snapshot_job,
    save_company_risk_snapshot,
)


@dataclass
class _StubScore:
    ticker: str
    total_risk_score: float
    risk_band: str
    port_count: int = 0
    weighted_deficit_days: float = 0.0
    critical_port_count: int = 0
    top_problem_ports: list = field(default_factory=list)


# ── Path helpers ────────────────────────────────────────────────────────


def test_company_risk_dir_for_uses_iso_date(tmp_path) -> None:
    d = company_risk_dir_for(date(2026, 5, 26), root=tmp_path)
    assert d == tmp_path / "2026-05-26"


def test_company_risk_dir_default_root_under_cache() -> None:
    d = company_risk_dir_for(date(2026, 5, 26))
    assert "company_risk_history" in str(d)


# ── Save / load round-trip ──────────────────────────────────────────────


def test_save_writes_one_line_per_ticker(tmp_path) -> None:
    scores = [
        _StubScore("ZIM", 15.0, "Low", port_count=8),
        _StubScore("MATX", 45.0, "Elevated", port_count=5),
        _StubScore("DAC", 72.0, "High", port_count=3),
    ]
    path, n = save_company_risk_snapshot(
        snapshot_date=date(2026, 5, 26),
        root=tmp_path, scores=scores,
    )
    assert path.exists()
    assert n > 0
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3
    for line in lines:
        blob = json.loads(line)
        assert "ticker" in blob
        assert "risk_band" in blob


def test_save_creates_missing_parent_dirs(tmp_path) -> None:
    nested = tmp_path / "deep" / "nested"
    save_company_risk_snapshot(
        snapshot_date=date(2026, 5, 26), root=nested,
        scores=[_StubScore("A", 10.0, "Low")],
    )
    assert nested.exists()


def test_load_filters_by_ticker(tmp_path) -> None:
    """Saving multiple tickers per day; load only returns the requested one."""
    for delta in range(3):
        save_company_risk_snapshot(
            snapshot_date=date(2026, 5, 22 + delta),
            root=tmp_path,
            scores=[
                _StubScore("ZIM", 15.0 + delta, "Low"),
                _StubScore("MATX", 45.0 + delta, "Elevated"),
            ],
        )
    history = load_company_risk_for_ticker(
        "ZIM", window_days=10, today=date(2026, 5, 26), root=tmp_path,
    )
    assert len(history) == 3
    for entry in history:
        assert entry["ticker"] == "ZIM"
        assert "date_iso" in entry


def test_load_returns_empty_when_no_history(tmp_path) -> None:
    history = load_company_risk_for_ticker(
        "X", window_days=14, today=date(2026, 5, 26), root=tmp_path,
    )
    assert history == []


def test_load_ignores_missing_dates(tmp_path) -> None:
    save_company_risk_snapshot(
        snapshot_date=date(2026, 5, 22), root=tmp_path,
        scores=[_StubScore("A", 10.0, "Low")],
    )
    save_company_risk_snapshot(
        snapshot_date=date(2026, 5, 24), root=tmp_path,
        scores=[_StubScore("A", 12.0, "Low")],
    )
    history = load_company_risk_for_ticker(
        "A", window_days=10, today=date(2026, 5, 26), root=tmp_path,
    )
    assert len(history) == 2


# ── list_company_risk_dates ────────────────────────────────────────────


def test_list_dates_returns_only_dirs_with_jsonl(tmp_path) -> None:
    (tmp_path / "2026-05-24").mkdir()
    (tmp_path / "2026-05-25").mkdir()
    (tmp_path / "2026-05-25" / "company_risk.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "manual-notes").mkdir()
    dates = list_company_risk_dates(root=tmp_path)
    assert dates == [date(2026, 5, 25)]


# ── Band-transition detection ──────────────────────────────────────────


def test_detect_no_transitions_on_identical_snapshots(tmp_path) -> None:
    """Same bands today vs yesterday → empty transitions list."""
    same = [_StubScore("ZIM", 15.0, "Low"), _StubScore("MATX", 45.0, "Elevated")]
    save_company_risk_snapshot(
        snapshot_date=date(2026, 5, 25), root=tmp_path, scores=same,
    )
    save_company_risk_snapshot(
        snapshot_date=date(2026, 5, 26), root=tmp_path, scores=same,
    )
    out = detect_band_transitions(today=date(2026, 5, 26), root=tmp_path)
    assert out == []


def test_detect_flip_surfaces_in_transitions(tmp_path) -> None:
    """MATX flips from Elevated → High → one entry."""
    save_company_risk_snapshot(
        snapshot_date=date(2026, 5, 25), root=tmp_path,
        scores=[_StubScore("MATX", 45.0, "Elevated")],
    )
    save_company_risk_snapshot(
        snapshot_date=date(2026, 5, 26), root=tmp_path,
        scores=[_StubScore("MATX", 72.0, "High")],
    )
    out = detect_band_transitions(today=date(2026, 5, 26), root=tmp_path)
    assert len(out) == 1
    assert out[0]["ticker"] == "MATX"
    assert out[0]["prior_band"] == "Elevated"
    assert out[0]["current_band"] == "High"


def test_detect_returns_empty_with_no_prior_snapshot(tmp_path) -> None:
    save_company_risk_snapshot(
        snapshot_date=date(2026, 5, 26), root=tmp_path,
        scores=[_StubScore("A", 10.0, "Low")],
    )
    out = detect_band_transitions(today=date(2026, 5, 26), root=tmp_path)
    assert out == []


def test_detect_skips_tickers_only_in_today(tmp_path) -> None:
    """A newly-added ticker isn't a transition; it has no prior band."""
    save_company_risk_snapshot(
        snapshot_date=date(2026, 5, 25), root=tmp_path,
        scores=[_StubScore("OLD", 10.0, "Low")],
    )
    save_company_risk_snapshot(
        snapshot_date=date(2026, 5, 26), root=tmp_path,
        scores=[
            _StubScore("OLD", 10.0, "Low"),
            _StubScore("NEW", 75.0, "High"),
        ],
    )
    out = detect_band_transitions(today=date(2026, 5, 26), root=tmp_path)
    assert out == []


# ── run_daily_company_risk_snapshot_job ────────────────────────────────


def test_job_saves_snapshot_and_returns_ok(tmp_path) -> None:
    r = run_daily_company_risk_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    assert isinstance(r, CompanyRiskHistoryJobResult)
    assert r.ok is True
    assert r.snapshot_path
    assert Path(r.snapshot_path).exists()
    assert r.bytes_written > 0
    assert r.n_tickers_saved > 0


def test_job_returns_empty_transitions_on_first_run(tmp_path) -> None:
    r = run_daily_company_risk_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    assert r.ok is True
    assert r.band_transitions == []


def test_job_failure_during_save_returns_ok_false(
    monkeypatch, tmp_path,
) -> None:
    def _broken_save(*_a, **_kw):
        raise RuntimeError("simulated save failure")
    import processing.company_risk_history as crh
    monkeypatch.setattr(crh, "save_company_risk_snapshot", _broken_save)
    r = run_daily_company_risk_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    assert r.ok is False
    assert "save_company_risk_snapshot failed" in r.error_msg
