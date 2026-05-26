"""Defining-property tests for processing/multi_container_snapshot.py.

Per-test isolation: every test that touches the snapshot tree passes
``root=tmp_path`` so the on-disk state stays local to the test run.
Tests that exercise the failure-isolation contract monkeypatch
``processing.port_supply_history.run_daily_snapshot_job`` directly so
no upstream chain build is required for the negative paths.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from processing.multi_container_snapshot import (
    DEFAULT_CONTAINER_TYPES,
    MultiContainerResult,
    run_multi_container_snapshot_job,
    summarize_multi_container_health,
)
from processing.port_supply_history import SnapshotJobResult


# ── Module-level isolation helper ────────────────────────────────────────
#
# A few tests want SNAPSHOT_ROOT itself relocated under tmp_path (mimics
# the env-var-based isolation other projects use). The two-line fixture
# below does that via monkeypatch so we don't pollute the real cache/
# tree if a test forgets the root= keyword.


@pytest.fixture(autouse=True)
def _isolate_snapshot_root(tmp_path, monkeypatch):
    import processing.port_supply_history as psh

    monkeypatch.setattr(psh, "SNAPSHOT_ROOT", tmp_path / "_isolated_root")
    yield


# ── 1. Default container-type list ───────────────────────────────────────


def test_default_container_types_covers_dry_reefer_and_20ft() -> None:
    """The default list must include all three slices the operator
    dashboard renders side-by-side. Other lists (HC, tank) are tracked
    by equipment_tracker but not part of the daily fan-out."""
    assert "40FT_DRY" in DEFAULT_CONTAINER_TYPES
    assert "40FT_REEFER" in DEFAULT_CONTAINER_TYPES
    assert "20FT_DRY" in DEFAULT_CONTAINER_TYPES


def test_default_run_produces_one_result_per_default_container(
    tmp_path,
) -> None:
    """No container_types arg → fan-out runs across DEFAULT_CONTAINER_TYPES
    end-to-end. Every default type must show up in per_container_results."""
    r = run_multi_container_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    assert isinstance(r, MultiContainerResult)
    assert r.today_iso == "2026-05-26"
    assert set(r.per_container_results.keys()) == set(DEFAULT_CONTAINER_TYPES)
    for ct, single in r.per_container_results.items():
        assert isinstance(single, SnapshotJobResult)
        assert single.ok is True
        assert single.container_type == ct


# ── 2. Custom container-type list ────────────────────────────────────────


def test_custom_single_container_list_runs_only_that_one(tmp_path) -> None:
    r = run_multi_container_snapshot_job(
        container_types=["40FT_DRY"],
        today=date(2026, 5, 26),
        root=tmp_path,
    )
    assert set(r.per_container_results.keys()) == {"40FT_DRY"}
    assert r.any_failed is False
    assert r.total_bytes_written > 0


def test_empty_container_list_yields_empty_result_no_failure(
    tmp_path,
) -> None:
    """Passing an empty list is a no-op — nothing was attempted, so
    any_failed stays False and the per-container dict is empty."""
    r = run_multi_container_snapshot_job(
        container_types=[],
        today=date(2026, 5, 26),
        root=tmp_path,
    )
    assert r.per_container_results == {}
    assert r.any_failed is False
    assert r.total_bytes_written == 0


# ── 3. Failure isolation — one bad container can't poison the rest ───────


def test_single_container_failure_does_not_affect_others(
    tmp_path, monkeypatch,
) -> None:
    """Monkeypatch run_daily_snapshot_job to fail for 40FT_REEFER but
    succeed for the others. The result must still include all three
    container types and the failure must surface only on the reefer slot.
    """
    import processing.port_supply_history as psh

    real_run = psh.run_daily_snapshot_job

    def _selective_failure(*, container_type, **kw):
        if container_type == "40FT_REEFER":
            return SnapshotJobResult(
                ok=False,
                today=kw.get("today").isoformat() if kw.get("today") else "",
                container_type=container_type,
                error_msg="simulated reefer build failure",
            )
        return real_run(container_type=container_type, **kw)

    monkeypatch.setattr(psh, "run_daily_snapshot_job", _selective_failure)

    r = run_multi_container_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    # All three slots present
    assert set(r.per_container_results.keys()) == set(DEFAULT_CONTAINER_TYPES)
    # Reefer failed
    assert r.per_container_results["40FT_REEFER"].ok is False
    assert "simulated reefer build failure" in (
        r.per_container_results["40FT_REEFER"].error_msg
    )
    # Other two succeeded
    assert r.per_container_results["40FT_DRY"].ok is True
    assert r.per_container_results["20FT_DRY"].ok is True


def test_run_daily_snapshot_job_raising_is_caught(
    tmp_path, monkeypatch,
) -> None:
    """Underlying job is supposed to be defensive — but if a future
    refactor (or a test monkeypatch) makes it raise, the coordinator
    must catch + synthesize a failed result rather than aborting the
    whole loop."""
    import processing.port_supply_history as psh

    def _always_raises(**_kw):
        raise RuntimeError("simulated catastrophic failure")

    monkeypatch.setattr(psh, "run_daily_snapshot_job", _always_raises)

    r = run_multi_container_snapshot_job(
        container_types=["40FT_DRY", "20FT_DRY"],
        today=date(2026, 5, 26),
        root=tmp_path,
    )
    assert r.any_failed is True
    for ct in ("40FT_DRY", "20FT_DRY"):
        single = r.per_container_results[ct]
        assert single.ok is False
        assert "simulated catastrophic failure" in single.error_msg
        assert single.container_type == ct


# ── 4. any_failed flag ───────────────────────────────────────────────────


def test_any_failed_false_when_all_succeed(tmp_path) -> None:
    r = run_multi_container_snapshot_job(
        container_types=["40FT_DRY"],
        today=date(2026, 5, 26),
        root=tmp_path,
    )
    assert r.any_failed is False


def test_any_failed_flips_true_when_any_result_fails(
    tmp_path, monkeypatch,
) -> None:
    import processing.port_supply_history as psh

    def _all_fail(*, container_type, **kw):
        return SnapshotJobResult(
            ok=False,
            today=kw.get("today").isoformat() if kw.get("today") else "",
            container_type=container_type,
            error_msg="forced failure",
        )

    monkeypatch.setattr(psh, "run_daily_snapshot_job", _all_fail)

    r = run_multi_container_snapshot_job(
        container_types=["40FT_DRY", "20FT_DRY"],
        today=date(2026, 5, 26),
        root=tmp_path,
    )
    assert r.any_failed is True
    assert r.total_bytes_written == 0


# ── 5. total_bytes_written accounting ────────────────────────────────────


def test_total_bytes_written_is_sum_of_per_container_bytes(
    tmp_path, monkeypatch,
) -> None:
    """Stub out the underlying job to return fixed byte counts so the
    sum is deterministic."""
    import processing.port_supply_history as psh

    bytes_table = {
        "40FT_DRY":    1234,
        "40FT_REEFER":  567,
        "20FT_DRY":     890,
    }

    def _fixed_bytes(*, container_type, **kw):
        return SnapshotJobResult(
            ok=True,
            today=kw.get("today").isoformat() if kw.get("today") else "",
            container_type=container_type,
            snapshot_path=str(tmp_path / f"{container_type}.csv"),
            bytes_written=bytes_table[container_type],
        )

    monkeypatch.setattr(psh, "run_daily_snapshot_job", _fixed_bytes)

    r = run_multi_container_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    assert r.total_bytes_written == sum(bytes_table.values())


def test_failed_containers_contribute_zero_bytes(
    tmp_path, monkeypatch,
) -> None:
    """A failed container's bytes_written must NOT roll into the total
    even if the failed SnapshotJobResult happens to carry a non-zero
    bytes_written value (defensive accounting)."""
    import processing.port_supply_history as psh

    def _mixed(*, container_type, **kw):
        if container_type == "40FT_DRY":
            return SnapshotJobResult(
                ok=True,
                today=kw.get("today").isoformat() if kw.get("today") else "",
                container_type=container_type,
                bytes_written=100,
            )
        return SnapshotJobResult(
            ok=False,
            today=kw.get("today").isoformat() if kw.get("today") else "",
            container_type=container_type,
            bytes_written=999,   # would be counted if accounting was naive
            error_msg="failed",
        )

    monkeypatch.setattr(psh, "run_daily_snapshot_job", _mixed)

    r = run_multi_container_snapshot_job(
        container_types=["40FT_DRY", "40FT_REEFER"],
        today=date(2026, 5, 26),
        root=tmp_path,
    )
    # Only the ok=True 40FT_DRY counts — 100, not 1099.
    assert r.total_bytes_written == 100
    assert r.any_failed is True


# ── 6. summarize_multi_container_health ──────────────────────────────────


def test_summarize_includes_per_container_status(tmp_path) -> None:
    """The summary string must mention every container by name + ok/fail."""
    r = run_multi_container_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    text = summarize_multi_container_health(r)
    for ct in DEFAULT_CONTAINER_TYPES:
        assert ct in text


def test_summarize_marks_failures(tmp_path, monkeypatch) -> None:
    """When a container failed, the summary line for it must flag it
    so a log reader sees the failure inline."""
    import processing.port_supply_history as psh

    def _reefer_fails(*, container_type, **kw):
        if container_type == "40FT_REEFER":
            return SnapshotJobResult(
                ok=False,
                container_type=container_type,
                error_msg="boom",
            )
        return SnapshotJobResult(
            ok=True,
            container_type=container_type,
            bytes_written=100,
        )

    monkeypatch.setattr(psh, "run_daily_snapshot_job", _reefer_fails)

    r = run_multi_container_snapshot_job(
        today=date(2026, 5, 26), root=tmp_path,
    )
    text = summarize_multi_container_health(r)
    assert "FAILED" in text
    assert "boom" in text
    assert "40FT_REEFER" in text


def test_summarize_shows_aggregate_byte_count(tmp_path) -> None:
    r = run_multi_container_snapshot_job(
        container_types=["40FT_DRY"],
        today=date(2026, 5, 26),
        root=tmp_path,
    )
    text = summarize_multi_container_health(r)
    # Comma-formatted bytes appear in the header line.
    assert f"{r.total_bytes_written:,}B" in text


def test_summarize_header_calls_out_failure_state(
    tmp_path, monkeypatch,
) -> None:
    import processing.port_supply_history as psh

    def _all_fail(*, container_type, **kw):
        return SnapshotJobResult(
            ok=False,
            container_type=container_type,
            error_msg="bad",
        )

    monkeypatch.setattr(psh, "run_daily_snapshot_job", _all_fail)

    r = run_multi_container_snapshot_job(
        container_types=["40FT_DRY"],
        today=date(2026, 5, 26),
        root=tmp_path,
    )
    text = summarize_multi_container_health(r)
    assert "with failures" in text


# ── 7. min_diff_delta_days propagates through ────────────────────────────


def test_min_diff_delta_days_passes_through_to_underlying_job(
    tmp_path, monkeypatch,
) -> None:
    """The coordinator must forward min_diff_delta_days verbatim so
    the operator's --min-delta knob remains effective in fan-out mode."""
    import processing.port_supply_history as psh

    seen: dict[str, float] = {}

    def _capture(*, container_type, min_diff_delta_days, **kw):
        seen[container_type] = min_diff_delta_days
        return SnapshotJobResult(
            ok=True,
            container_type=container_type,
            bytes_written=1,
        )

    monkeypatch.setattr(psh, "run_daily_snapshot_job", _capture)

    run_multi_container_snapshot_job(
        container_types=["40FT_DRY", "20FT_DRY"],
        today=date(2026, 5, 26),
        root=tmp_path,
        min_diff_delta_days=2.5,
    )
    assert seen == {"40FT_DRY": 2.5, "20FT_DRY": 2.5}


# ── 8. today defaulting ──────────────────────────────────────────────────


def test_today_defaults_to_utc_today(tmp_path) -> None:
    """When today is None, today_iso must be set to a parseable ISO
    date — we don't pin the exact value (would be flaky across UTC
    rollover), just that it round-trips through date.fromisoformat."""
    r = run_multi_container_snapshot_job(
        container_types=["40FT_DRY"], root=tmp_path,
    )
    parsed = date.fromisoformat(r.today_iso)
    assert parsed.year >= 2026
