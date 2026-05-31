"""Tests for worker.scheduler — daily investor briefing job runner.

Covers:
  - ReportJobResult dataclass shape & defaults
  - run_daily_briefing_job happy path (build + save mocked) → success=True,
    duration_s > 0, populated report_id + file_path, empty error_msg
  - run_daily_briefing_job when build_investor_report raises → success=False
    with error_msg surfacing the exception
  - run_daily_briefing_job when save_report returns None → success=False
    with explicit "save_report returned None" error_msg
  - run_daily_briefing_job with push_to_channels=True calls deliver_pending
    for every enabled channel and skips disabled ones
  - run_daily_briefing_job with push_to_channels=False (default) never
    touches the delivery layer
  - main() exits 0 on success and 1 on failure (system-exit pattern)
  - load_data_bundle returns the expected keys even when every data
    source is unavailable

All external side effects (Anthropic API, FRED HTTP, SQLite writes
through save_report, requests.post in deliver_pending) are mocked. Real
PDF rendering is never invoked.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from worker import scheduler
from worker.scheduler import (
    ReportJobResult,
    load_data_bundle,
    main,
    run_daily_briefing_job,
    run_telemetry_prune_job,
)


# ─── Fixture: isolate SQLite + reports dir per test ──────────────────────────

@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch, tmp_path):
    """Per-test SQLite DB + reports dir so no real cache is touched."""
    from state import db as state_db
    from utils import report_history as rh

    tmp_reports = tmp_path / "reports"
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_reports)
    monkeypatch.setattr(rh, "_INDEX_FILE", tmp_reports / "report_index.json")
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ────────────────────────────────────────────────────────────────

class _FakeReport:
    """Stand-in for an InvestorReport. Only needs to be passed-through."""

    def __init__(self) -> None:
        self.generated_at = datetime.now(timezone.utc).isoformat()
        self.report_date = "May 21, 2026"
        self.data_quality = "FULL"


class _FakeMeta:
    """Stand-in for utils.report_history.ReportMeta."""

    def __init__(
        self,
        report_id: str = "test-report-123",
        file_path: str = "/tmp/fake_report.html",
    ) -> None:
        self.report_id = report_id
        self.file_path = file_path


def _stub_bundle() -> dict:
    """A minimal bundle whose values are never actually inspected because
    build_investor_report is mocked."""
    return {
        "port_results": [],
        "route_results": [],
        "insights": [],
        "freight_data": {},
        "macro_data": {},
        "stock_data": {},
        "news_items": [],
        "source": "stub",
    }


# ─── ReportJobResult dataclass ──────────────────────────────────────────────

def test_report_job_result_default_fields() -> None:
    """Defaults match the contract callers depend on."""
    result = ReportJobResult()
    assert result.report_id == ""
    assert result.file_path == ""
    assert result.success is False
    assert result.duration_s == 0.0
    assert result.error_msg == ""


def test_report_job_result_populated_fields() -> None:
    result = ReportJobResult(
        report_id="abc",
        file_path="/tmp/x.html",
        success=True,
        duration_s=1.5,
        error_msg="",
    )
    as_dict = asdict(result)
    assert as_dict == {
        "report_id": "abc",
        "file_path": "/tmp/x.html",
        "success": True,
        "duration_s": 1.5,
        "error_msg": "",
    }


# ─── run_daily_briefing_job — happy path ────────────────────────────────────

def test_run_daily_briefing_job_happy_path(monkeypatch) -> None:
    """build + render + save all succeed; success=True with populated fields."""
    fake_report = _FakeReport()
    fake_meta = _FakeMeta(report_id="rid-happy", file_path="/tmp/happy.html")

    build_mock = MagicMock(return_value=fake_report)
    render_mock = MagicMock(return_value="<html>stub</html>")
    save_mock = MagicMock(return_value=fake_meta)

    monkeypatch.setattr(
        "processing.investor_report_engine.build_investor_report", build_mock
    )
    monkeypatch.setattr(
        "utils.investor_report_html.render_investor_report_html", render_mock
    )
    monkeypatch.setattr("utils.report_history.save_report", save_mock)

    result = run_daily_briefing_job(_stub_bundle())

    assert result.success is True
    assert result.report_id == "rid-happy"
    assert result.file_path == "/tmp/happy.html"
    assert result.error_msg == ""
    assert result.duration_s >= 0.0
    build_mock.assert_called_once()
    render_mock.assert_called_once_with(fake_report)
    save_mock.assert_called_once()


# ─── run_daily_briefing_job — failure modes ─────────────────────────────────

def test_run_daily_briefing_job_build_failure(monkeypatch) -> None:
    """build_investor_report raising bubbles up as success=False + error_msg."""
    build_mock = MagicMock(side_effect=RuntimeError("engine exploded"))
    save_mock = MagicMock()
    monkeypatch.setattr(
        "processing.investor_report_engine.build_investor_report", build_mock
    )
    monkeypatch.setattr("utils.report_history.save_report", save_mock)

    result = run_daily_briefing_job(_stub_bundle())

    assert result.success is False
    assert result.report_id == ""
    assert result.file_path == ""
    assert "engine exploded" in result.error_msg
    save_mock.assert_not_called()


def test_run_daily_briefing_job_save_failure(monkeypatch) -> None:
    """save_report returning None marks the job as failed."""
    build_mock = MagicMock(return_value=_FakeReport())
    render_mock = MagicMock(return_value="<html>x</html>")
    save_mock = MagicMock(return_value=None)

    monkeypatch.setattr(
        "processing.investor_report_engine.build_investor_report", build_mock
    )
    monkeypatch.setattr(
        "utils.investor_report_html.render_investor_report_html", render_mock
    )
    monkeypatch.setattr("utils.report_history.save_report", save_mock)

    result = run_daily_briefing_job(_stub_bundle())

    assert result.success is False
    assert result.report_id == ""
    assert result.file_path == ""
    assert "save_report returned None" in result.error_msg


def test_run_daily_briefing_job_render_failure(monkeypatch) -> None:
    """An exception in render_investor_report_html is captured, not raised."""
    build_mock = MagicMock(return_value=_FakeReport())
    render_mock = MagicMock(side_effect=ValueError("bad template"))
    save_mock = MagicMock()

    monkeypatch.setattr(
        "processing.investor_report_engine.build_investor_report", build_mock
    )
    monkeypatch.setattr(
        "utils.investor_report_html.render_investor_report_html", render_mock
    )
    monkeypatch.setattr("utils.report_history.save_report", save_mock)

    result = run_daily_briefing_job(_stub_bundle())

    assert result.success is False
    assert "bad template" in result.error_msg
    save_mock.assert_not_called()


# ─── push_to_channels behavior ──────────────────────────────────────────────

def test_run_daily_briefing_job_push_calls_deliver_for_enabled_channels(
    monkeypatch,
) -> None:
    """push_to_channels=True dispatches deliver_pending for every enabled channel."""
    fake_meta = _FakeMeta()
    monkeypatch.setattr(
        "processing.investor_report_engine.build_investor_report",
        MagicMock(return_value=_FakeReport()),
    )
    monkeypatch.setattr(
        "utils.investor_report_html.render_investor_report_html",
        MagicMock(return_value="<html/>"),
    )
    monkeypatch.setattr(
        "utils.report_history.save_report", MagicMock(return_value=fake_meta)
    )

    # Two enabled channels, one disabled — only the enabled ones should
    # receive a deliver_pending call.
    from engine.alert_delivery import DeliveryChannel

    ch1 = DeliveryChannel(
        channel_id="c1",
        name="Trading Desk",
        kind="slack",
        target="https://example/webhook/1",
        severity_threshold="MEDIUM",
        enabled=True,
    )
    ch2 = DeliveryChannel(
        channel_id="c2",
        name="Risk Desk",
        kind="slack",
        target="https://example/webhook/2",
        severity_threshold="HIGH",
        enabled=True,
    )
    ch3 = DeliveryChannel(
        channel_id="c3",
        name="Archived",
        kind="slack",
        target="https://example/webhook/3",
        severity_threshold="LOW",
        enabled=False,
    )

    load_mock = MagicMock(return_value=[ch1, ch2, ch3])
    deliver_mock = MagicMock(return_value=[])
    monkeypatch.setattr("engine.alert_delivery.load_channels", load_mock)
    monkeypatch.setattr("engine.alert_delivery.deliver_pending", deliver_mock)

    result = run_daily_briefing_job(_stub_bundle(), push_to_channels=True)

    assert result.success is True
    load_mock.assert_called_once()
    # Two enabled → two deliver_pending calls
    assert deliver_mock.call_count == 2
    delivered_channels = [call.args[0] for call in deliver_mock.call_args_list]
    assert {c.channel_id for c in delivered_channels} == {"c1", "c2"}


def test_run_daily_briefing_job_no_push_skips_delivery(monkeypatch) -> None:
    """push_to_channels=False never touches the delivery layer."""
    fake_meta = _FakeMeta()
    monkeypatch.setattr(
        "processing.investor_report_engine.build_investor_report",
        MagicMock(return_value=_FakeReport()),
    )
    monkeypatch.setattr(
        "utils.investor_report_html.render_investor_report_html",
        MagicMock(return_value="<html/>"),
    )
    monkeypatch.setattr(
        "utils.report_history.save_report", MagicMock(return_value=fake_meta)
    )

    load_mock = MagicMock()
    deliver_mock = MagicMock()
    monkeypatch.setattr("engine.alert_delivery.load_channels", load_mock)
    monkeypatch.setattr("engine.alert_delivery.deliver_pending", deliver_mock)

    result = run_daily_briefing_job(_stub_bundle(), push_to_channels=False)

    assert result.success is True
    load_mock.assert_not_called()
    deliver_mock.assert_not_called()


def test_run_daily_briefing_job_delivery_failure_does_not_fail_job(
    monkeypatch,
) -> None:
    """A failing channel push must NOT flip success to False; the report
    was still built + saved."""
    fake_meta = _FakeMeta()
    monkeypatch.setattr(
        "processing.investor_report_engine.build_investor_report",
        MagicMock(return_value=_FakeReport()),
    )
    monkeypatch.setattr(
        "utils.investor_report_html.render_investor_report_html",
        MagicMock(return_value="<html/>"),
    )
    monkeypatch.setattr(
        "utils.report_history.save_report", MagicMock(return_value=fake_meta)
    )

    from engine.alert_delivery import DeliveryChannel

    ch = DeliveryChannel(
        channel_id="c1",
        name="Broken",
        kind="slack",
        target="https://example/webhook",
        severity_threshold="MEDIUM",
        enabled=True,
    )
    monkeypatch.setattr(
        "engine.alert_delivery.load_channels", MagicMock(return_value=[ch])
    )
    monkeypatch.setattr(
        "engine.alert_delivery.deliver_pending",
        MagicMock(side_effect=RuntimeError("webhook down")),
    )

    result = run_daily_briefing_job(_stub_bundle(), push_to_channels=True)

    # Report was saved → still a success
    assert result.success is True
    assert result.report_id == fake_meta.report_id


# ─── main() CLI ─────────────────────────────────────────────────────────────

def test_main_exits_zero_on_success(monkeypatch, capsys) -> None:
    """A successful run yields a SystemExit(0)."""
    monkeypatch.setattr(scheduler, "load_data_bundle", lambda: _stub_bundle())
    monkeypatch.setattr(
        scheduler,
        "run_daily_briefing_job",
        lambda bundle, *, push_to_channels=False: ReportJobResult(
            report_id="rid",
            file_path="/tmp/x.html",
            success=True,
            duration_s=0.1,
            error_msg="",
        ),
    )
    monkeypatch.setattr(sys, "argv", ["worker.scheduler"])

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["success"] is True
    assert parsed["report_id"] == "rid"


def test_main_exits_one_on_failure(monkeypatch, capsys) -> None:
    """A failed run yields a SystemExit(1)."""
    monkeypatch.setattr(scheduler, "load_data_bundle", lambda: _stub_bundle())
    monkeypatch.setattr(
        scheduler,
        "run_daily_briefing_job",
        lambda bundle, *, push_to_channels=False: ReportJobResult(
            report_id="",
            file_path="",
            success=False,
            duration_s=0.2,
            error_msg="boom",
        ),
    )
    monkeypatch.setattr(sys, "argv", ["worker.scheduler"])

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["success"] is False
    assert parsed["error_msg"] == "boom"


def test_main_push_flag_is_forwarded(monkeypatch) -> None:
    """``--push`` propagates into run_daily_briefing_job as push_to_channels=True."""
    monkeypatch.setattr(scheduler, "load_data_bundle", lambda: _stub_bundle())

    captured = {}

    def fake_runner(bundle, *, push_to_channels=False):
        captured["push"] = push_to_channels
        return ReportJobResult(
            report_id="rid",
            file_path="/tmp/x.html",
            success=True,
            duration_s=0.1,
            error_msg="",
        )

    monkeypatch.setattr(scheduler, "run_daily_briefing_job", fake_runner)
    monkeypatch.setattr(sys, "argv", ["worker.scheduler", "--push"])

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0
    assert captured["push"] is True


# ─── per-job cadence gates (#4) ───────────────────────────────────────────

def test_job_due_first_run_then_within_interval() -> None:
    """A never-run job is due (and stamps); a second check within the
    interval is NOT due."""
    from datetime import datetime, timezone
    from worker.scheduler import _job_due

    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert _job_due("cadence-x", 3600, now=now) is True   # never run → due
    assert _job_due("cadence-x", 3600, now=now) is False  # within 1h → not


def test_job_due_again_after_interval_elapsed() -> None:
    from datetime import datetime, timedelta, timezone
    from worker.scheduler import _job_due

    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert _job_due("cadence-y", 3600, now=t0) is True
    assert _job_due("cadence-y", 3600, now=t0 + timedelta(minutes=30)) is False
    assert _job_due("cadence-y", 3600, now=t0 + timedelta(hours=2)) is True


def test_job_due_force_bypasses_the_gate() -> None:
    from datetime import datetime, timezone
    from worker.scheduler import _job_due

    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert _job_due("cadence-z", 3600, now=now) is True
    assert _job_due("cadence-z", 3600, now=now) is False           # gated
    assert _job_due("cadence-z", 3600, now=now, force=True) is True  # forced


def test_main_gates_heavy_jobs_but_runs_sla_jobs_every_pass(monkeypatch) -> None:
    """The cadence fix: across two back-to-back main() invocations the heavy
    briefing build runs ONCE (daily gate), while the SLA jobs (escalation,
    delivery retry) run on BOTH passes."""
    monkeypatch.setattr(scheduler, "load_data_bundle", lambda: _stub_bundle())
    counts = {"briefing": 0, "escalation": 0, "retry": 0}

    def fake_briefing(bundle, *, push_to_channels=False):
        counts["briefing"] += 1
        return ReportJobResult(report_id="r", file_path="/tmp/x",
                               success=True, duration_s=0.1, error_msg="")

    def fake_esc(*a, **k):
        counts["escalation"] += 1
        return {}

    def fake_retry(*a, **k):
        counts["retry"] += 1
        return {}

    monkeypatch.setattr(scheduler, "run_daily_briefing_job", fake_briefing)
    monkeypatch.setattr(scheduler, "run_alert_escalation_job", fake_esc)
    monkeypatch.setattr(scheduler, "run_delivery_retry_job", fake_retry)
    monkeypatch.setattr(sys, "argv", ["worker.scheduler"])

    for _ in range(2):
        with pytest.raises(SystemExit):
            main()

    assert counts["briefing"] == 1     # heavy job gated daily → ran once
    assert counts["escalation"] == 2   # SLA job → ran every pass
    assert counts["retry"] == 2        # SLA job → ran every pass


# ─── load_data_bundle ──────────────────────────────────────────────────────

def test_load_data_bundle_returns_expected_keys() -> None:
    """Even when every data source is unavailable, the bundle still has
    the keys downstream callers expect."""
    bundle = load_data_bundle()
    expected_keys = {
        "port_results",
        "route_results",
        "insights",
        "freight_data",
        "macro_data",
        "stock_data",
        "news_items",
        "source",
    }
    assert expected_keys.issubset(bundle.keys())
    # The source field is a non-empty diagnostic string
    assert isinstance(bundle["source"], str) and bundle["source"]


def test_load_data_bundle_does_not_import_streamlit() -> None:
    """The worker must never pull streamlit into the process. If
    load_data_bundle accidentally imports it, this test will catch it."""
    import worker.scheduler as ws

    # If `streamlit` had been imported by anything in worker.scheduler's
    # transitive chain at module import time, it would already be in
    # sys.modules. We can't strictly forbid it (a test fixture might
    # pull it in), but we can confirm worker.scheduler itself does not
    # have a streamlit attribute baked in.
    assert not hasattr(ws, "st")


# ─── run_telemetry_prune_job ────────────────────────────────────────────────

def test_run_telemetry_prune_job_returns_int(monkeypatch) -> None:
    """Returns the int count from prune_old_calls; never raises."""
    prune_mock = MagicMock(return_value=7)
    monkeypatch.setattr("engine.llm_telemetry.prune_old_calls", prune_mock)

    result = run_telemetry_prune_job(retention_days=42)

    assert result == 7
    prune_mock.assert_called_once_with(retention_days=42)


def test_run_telemetry_prune_job_swallows_errors(monkeypatch) -> None:
    """A prune_old_calls exception must NOT propagate; returns 0."""
    prune_mock = MagicMock(side_effect=RuntimeError("db wedged"))
    monkeypatch.setattr("engine.llm_telemetry.prune_old_calls", prune_mock)

    # Must not raise.
    result = run_telemetry_prune_job()
    assert result == 0


# ─── main() runs both jobs (briefing + prune) ──────────────────────────────

def test_main_calls_both_briefing_and_prune_in_order(monkeypatch) -> None:
    """main() invokes run_daily_briefing_job FIRST, then run_telemetry_prune_job."""
    monkeypatch.setattr(scheduler, "load_data_bundle", lambda: _stub_bundle())

    call_order: list[str] = []

    def fake_briefing(bundle, *, push_to_channels=False):
        call_order.append("briefing")
        return ReportJobResult(
            report_id="rid",
            file_path="/tmp/x.html",
            success=True,
            duration_s=0.1,
            error_msg="",
        )

    def fake_prune(retention_days: int = 90):
        call_order.append("prune")
        return 0

    monkeypatch.setattr(scheduler, "run_daily_briefing_job", fake_briefing)
    monkeypatch.setattr(scheduler, "run_telemetry_prune_job", fake_prune)
    monkeypatch.setattr(sys, "argv", ["worker.scheduler", "--push"])

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0
    # Briefing runs first, then prune.
    assert call_order == ["briefing", "prune"]


def test_main_prune_failure_does_not_block_successful_briefing(monkeypatch) -> None:
    """A raise inside run_telemetry_prune_job must NOT flip the briefing's
    exit code — the report ran successfully and that's what matters."""
    monkeypatch.setattr(scheduler, "load_data_bundle", lambda: _stub_bundle())

    monkeypatch.setattr(
        scheduler,
        "run_daily_briefing_job",
        lambda bundle, *, push_to_channels=False: ReportJobResult(
            report_id="rid",
            file_path="/tmp/x.html",
            success=True,
            duration_s=0.1,
            error_msg="",
        ),
    )
    monkeypatch.setattr(
        scheduler,
        "run_telemetry_prune_job",
        MagicMock(side_effect=RuntimeError("prune blew up")),
    )
    monkeypatch.setattr(sys, "argv", ["worker.scheduler", "--push"])

    with pytest.raises(SystemExit) as excinfo:
        main()

    # Exit code is still 0 — briefing succeeded; prune is best-effort.
    assert excinfo.value.code == 0


# ─── run_bulk_export_prune_job ──────────────────────────────────────────────

def test_run_bulk_export_prune_job_returns_int(monkeypatch) -> None:
    """Returns the int count from prune_old_exports; never raises."""
    prune_mock = MagicMock(return_value=3)
    monkeypatch.setattr("utils.bulk_export.prune_old_exports", prune_mock)

    from worker.scheduler import run_bulk_export_prune_job
    result = run_bulk_export_prune_job(keep_n=7)

    assert result == 3
    prune_mock.assert_called_once_with(keep_n=7)


def test_run_bulk_export_prune_job_swallows_errors(monkeypatch) -> None:
    """A prune_old_exports exception must NOT propagate; returns 0."""
    prune_mock = MagicMock(side_effect=RuntimeError("disk wedged"))
    monkeypatch.setattr("utils.bulk_export.prune_old_exports", prune_mock)

    from worker.scheduler import run_bulk_export_prune_job
    # Must not raise.
    result = run_bulk_export_prune_job()
    assert result == 0


def test_main_invokes_bulk_export_prune_after_health_prune(monkeypatch) -> None:
    """main() invokes run_bulk_export_prune_job AFTER run_health_prune_job."""
    monkeypatch.setattr(scheduler, "load_data_bundle", lambda: _stub_bundle())

    call_order: list[str] = []

    def fake_briefing(bundle, *, push_to_channels=False):
        call_order.append("briefing")
        return ReportJobResult(
            report_id="rid",
            file_path="/tmp/x.html",
            success=True,
            duration_s=0.1,
            error_msg="",
        )

    monkeypatch.setattr(scheduler, "run_daily_briefing_job", fake_briefing)
    monkeypatch.setattr(scheduler, "run_telemetry_prune_job",
                        lambda *a, **k: (call_order.append("telemetry"), 0)[1])
    monkeypatch.setattr(scheduler, "run_perf_prune_job",
                        lambda *a, **k: (call_order.append("perf"), 0)[1])
    monkeypatch.setattr(scheduler, "run_snapshot_prune_job",
                        lambda *a, **k: (call_order.append("snapshot"), 0)[1])
    monkeypatch.setattr(scheduler, "run_health_ping_job",
                        lambda *a, **k: (call_order.append("health_ping"), [])[1])
    monkeypatch.setattr(scheduler, "run_health_prune_job",
                        lambda *a, **k: (call_order.append("health_prune"), 0)[1])
    monkeypatch.setattr(scheduler, "run_bulk_export_prune_job",
                        lambda *a, **k: (call_order.append("bulk_export_prune"), 0)[1])
    monkeypatch.setattr(sys, "argv", ["worker.scheduler"])

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0
    # Bulk export prune runs strictly after health prune.
    assert "bulk_export_prune" in call_order
    assert call_order.index("bulk_export_prune") > call_order.index("health_prune")


def test_main_bulk_export_prune_failure_does_not_block_briefing(monkeypatch) -> None:
    """A raise inside run_bulk_export_prune_job must NOT flip the briefing's
    exit code — the report ran successfully and that's what matters."""
    monkeypatch.setattr(scheduler, "load_data_bundle", lambda: _stub_bundle())
    monkeypatch.setattr(
        scheduler,
        "run_daily_briefing_job",
        lambda bundle, *, push_to_channels=False: ReportJobResult(
            report_id="rid", file_path="/tmp/x.html",
            success=True, duration_s=0.1, error_msg="",
        ),
    )
    monkeypatch.setattr(
        scheduler,
        "run_bulk_export_prune_job",
        MagicMock(side_effect=RuntimeError("export prune blew up")),
    )
    monkeypatch.setattr(sys, "argv", ["worker.scheduler"])

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0
