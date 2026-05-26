"""Defining-property tests for worker.scheduler.run_port_supply_snapshot_job."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

import worker.scheduler as sched


# ── Per-test isolation: redirect SNAPSHOT_ROOT to tmp_path ────────────────


@pytest.fixture(autouse=True)
def isolate_snapshot_root(monkeypatch, tmp_path):
    import processing.port_supply_history as psh
    monkeypatch.setattr(psh, "SNAPSHOT_ROOT", tmp_path)
    yield tmp_path


# ── 1. Wrapper return shape ──────────────────────────────────────────────


def test_returns_required_count_keys(isolate_snapshot_root) -> None:
    out = sched.run_port_supply_snapshot_job()
    assert set(out.keys()) >= {
        "ok", "saved_bytes", "diff_present",
        "severity_shifts", "entered_deficit",
        "exited_deficit", "deficit_moves",
    }


def test_first_run_returns_ok_with_no_diff(isolate_snapshot_root) -> None:
    """First-ever run has nothing prior to compare to — diff_present=False
    and every count is zero, but ok=True (snapshot landed)."""
    out = sched.run_port_supply_snapshot_job()
    assert out["ok"] is True
    assert out["saved_bytes"] > 0
    assert out["diff_present"] is False
    assert out["severity_shifts"] == 0
    assert out["entered_deficit"] == 0


# ── 2. Diff surfaces in the count dict when prior snapshot exists ────────


def test_diff_counts_surface_after_second_run(isolate_snapshot_root) -> None:
    """Save day 1, then run again for day 2 — the diff count keys
    must reflect whatever the comparator returned (zero on stable
    synth is also a valid count)."""
    from processing.port_supply_history import save_snapshot

    save_snapshot(snapshot_date=date(2026, 5, 25),
                  root=isolate_snapshot_root)

    # Patch run_daily_snapshot_job to use a fixed "today" so the test
    # is deterministic regardless of when it runs.
    import processing.port_supply_history as psh
    real_job = psh.run_daily_snapshot_job

    def _fixed_today_job(**kwargs):
        kwargs.setdefault("today", date(2026, 5, 26))
        return real_job(**kwargs)

    with patch.object(psh, "run_daily_snapshot_job", _fixed_today_job):
        out = sched.run_port_supply_snapshot_job()

    assert out["ok"] is True
    assert out["diff_present"] is True


# ── 3. Top-level failure surfaces ok=False but doesn't raise ─────────────


def test_wrapper_returns_ok_false_when_underlying_raises(monkeypatch) -> None:
    """If processing.port_supply_history.run_daily_snapshot_job raises,
    the wrapper logs + returns ok=False with zero counts rather than
    bubbling the exception up into the cron tick."""
    def _boom(**_kwargs):
        raise RuntimeError("simulated")

    import processing.port_supply_history as psh
    monkeypatch.setattr(psh, "run_daily_snapshot_job", _boom)

    out = sched.run_port_supply_snapshot_job()
    assert out["ok"] is False
    assert out["saved_bytes"] == 0
    assert out["diff_present"] is False


def test_wrapper_propagates_container_type_kwarg(
    isolate_snapshot_root, monkeypatch,
) -> None:
    """container_type kwarg must reach the underlying job verbatim."""
    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        # Return a minimal-shape SnapshotJobResult so the wrapper proceeds.
        from processing.port_supply_history import SnapshotJobResult
        return SnapshotJobResult(
            ok=True, today="2026-05-26", container_type=kwargs.get(
                "container_type", ""),
            snapshot_path="/tmp/x.csv", bytes_written=100,
            prior_snapshot_date="", diff=None, error_msg="",
        )

    import processing.port_supply_history as psh
    monkeypatch.setattr(psh, "run_daily_snapshot_job", _capture)

    sched.run_port_supply_snapshot_job(container_type="40FT_REEFER")
    assert captured.get("container_type") == "40FT_REEFER"


def test_wrapper_propagates_min_diff_delta_days_kwarg(
    isolate_snapshot_root, monkeypatch,
) -> None:
    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        from processing.port_supply_history import SnapshotJobResult
        return SnapshotJobResult(
            ok=True, today="2026-05-26", container_type="40FT_DRY",
            snapshot_path="/tmp/x.csv", bytes_written=100,
            prior_snapshot_date="", diff=None, error_msg="",
        )

    import processing.port_supply_history as psh
    monkeypatch.setattr(psh, "run_daily_snapshot_job", _capture)

    sched.run_port_supply_snapshot_job(min_diff_delta_days=2.5)
    assert captured.get("min_diff_delta_days") == 2.5


# ── 4. Wiring contract — main() guards the call with try/except ──────────


def test_main_function_calls_snapshot_job_under_try_except() -> None:
    """The main() entry point must wrap run_port_supply_snapshot_job in
    its own try/except so a failure doesn't take down the rest of the
    daily cron. Verified by reading the source — the canonical pattern
    every other run_*_job follows in this file."""
    import inspect
    src = inspect.getsource(sched.main)
    # The call site exists
    assert "run_port_supply_snapshot_job()" in src
    # ...and is wrapped in a try block (logger.warning pairs with the
    # try/except idiom used by every sibling job).
    assert "port supply snapshot step failed" in src