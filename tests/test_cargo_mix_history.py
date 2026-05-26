"""Defining-property tests for processing/cargo_mix_history.py."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from processing.cargo_mix_history import (
    CargoMixHistoryJobResult,
    cargo_mix_dir_for,
    list_cargo_mix_dates,
    load_cargo_mix_for_route,
    run_daily_cargo_mix_snapshot_job,
    save_cargo_mix_snapshot,
)


@dataclass
class _StubRoute:
    """Mimics ShippingRoute for the field this module touches."""
    id: str


# ── Path helpers ────────────────────────────────────────────────────────


def test_cargo_mix_dir_for_uses_iso_date(tmp_path) -> None:
    d = cargo_mix_dir_for(date(2026, 5, 26), root=tmp_path)
    assert d == tmp_path / "2026-05-26"


def test_cargo_mix_dir_default_root_under_cache() -> None:
    """Default root resolves under the project's cache/ tree."""
    d = cargo_mix_dir_for(date(2026, 5, 26))
    assert "cargo_mix_history" in str(d)


# ── Save / load round-trip ──────────────────────────────────────────────


def test_save_writes_one_line_per_route(tmp_path) -> None:
    routes = [_StubRoute("a"), _StubRoute("b"), _StubRoute("c")]
    path, n = save_cargo_mix_snapshot(
        snapshot_date=date(2026, 5, 26),
        root=tmp_path, routes=routes,
    )
    assert path.exists()
    assert n > 0
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3
    # Each line is parseable JSON with the canonical fields
    for line in lines:
        blob = json.loads(line)
        assert "route_id" in blob
        assert "mix" in blob


def test_save_creates_missing_parent_dirs(tmp_path) -> None:
    nested = tmp_path / "deep" / "nested"
    save_cargo_mix_snapshot(
        snapshot_date=date(2026, 5, 26),
        root=nested,
        routes=[_StubRoute("a")],
    )
    assert nested.exists()


def test_load_round_trips_through_save(tmp_path) -> None:
    """Save N days for one route; load_cargo_mix_for_route returns N
    mixes (one per day)."""
    routes = [_StubRoute("transpacific_eb")]
    for delta in range(5):
        save_cargo_mix_snapshot(
            snapshot_date=date(2026, 5, 21 + delta),
            root=tmp_path, routes=routes,
        )
    history = load_cargo_mix_for_route(
        "transpacific_eb",
        window_days=10,
        today=date(2026, 5, 26),
        root=tmp_path,
    )
    assert len(history) == 5
    # Each history entry is a dict[str, float]
    for mix in history:
        assert isinstance(mix, dict)
        for k, v in mix.items():
            assert isinstance(k, str)
            assert isinstance(v, float)


def test_load_ignores_missing_dates(tmp_path) -> None:
    """Save 2 of 5 days; load returns just those 2."""
    routes = [_StubRoute("X")]
    save_cargo_mix_snapshot(
        snapshot_date=date(2026, 5, 22),
        root=tmp_path, routes=routes,
    )
    save_cargo_mix_snapshot(
        snapshot_date=date(2026, 5, 24),
        root=tmp_path, routes=routes,
    )
    history = load_cargo_mix_for_route(
        "X", window_days=10, today=date(2026, 5, 26), root=tmp_path,
    )
    assert len(history) == 2


def test_load_filters_by_route_id(tmp_path) -> None:
    """Save with multiple routes per day; load only returns the requested route."""
    routes = [_StubRoute("A"), _StubRoute("B"), _StubRoute("C")]
    for delta in range(3):
        save_cargo_mix_snapshot(
            snapshot_date=date(2026, 5, 22 + delta),
            root=tmp_path, routes=routes,
        )
    history = load_cargo_mix_for_route(
        "B", window_days=10, today=date(2026, 5, 26), root=tmp_path,
    )
    assert len(history) == 3   # 3 days, only B's mix per day


def test_load_returns_empty_when_no_history(tmp_path) -> None:
    history = load_cargo_mix_for_route(
        "X", window_days=14, today=date(2026, 5, 26), root=tmp_path,
    )
    assert history == []


# ── list_cargo_mix_dates ───────────────────────────────────────────────


def test_list_dates_returns_only_iso_dirs_with_jsonl(tmp_path) -> None:
    # Plant some date dirs - only those with the JSONL file count
    (tmp_path / "2026-05-24").mkdir()
    (tmp_path / "2026-05-25").mkdir()
    (tmp_path / "2026-05-25" / "cargo_mix.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "manual-notes").mkdir()
    dates = list_cargo_mix_dates(root=tmp_path)
    assert dates == [date(2026, 5, 25)]   # only the one with the file


def test_list_dates_empty_when_root_missing(tmp_path) -> None:
    assert list_cargo_mix_dates(root=tmp_path / "does-not-exist") == []


# ── run_daily_cargo_mix_snapshot_job ───────────────────────────────────


def test_job_saves_snapshot_and_returns_ok(tmp_path) -> None:
    r = run_daily_cargo_mix_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    assert isinstance(r, CargoMixHistoryJobResult)
    assert r.ok is True
    assert r.snapshot_path
    assert Path(r.snapshot_path).exists()
    assert r.bytes_written > 0
    assert r.n_routes_saved > 0


def test_job_returns_empty_anomaly_list_when_no_history(tmp_path) -> None:
    """First-ever run has nothing to compare against → no anomalies."""
    r = run_daily_cargo_mix_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    assert r.ok is True
    assert r.anomaly_routes == []
    assert r.n_anomaly_routes == 0


def test_job_failure_during_save_returns_ok_false(monkeypatch, tmp_path) -> None:
    def _broken_save(*_a, **_kw):
        raise RuntimeError("simulated save failure")
    import processing.cargo_mix_history as cmh
    monkeypatch.setattr(cmh, "save_cargo_mix_snapshot", _broken_save)
    r = run_daily_cargo_mix_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    assert r.ok is False
    assert "save_cargo_mix_snapshot failed" in r.error_msg
