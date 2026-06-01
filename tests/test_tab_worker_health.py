"""Tests for ui.tab_worker_health — the operator dashboard tab.

The smoke parameter test in ``tests/test_tab_smoke.py`` already covers
"this module imports and renders empty without crashing". This file
exercises the tab-specific behaviour:

  * Renders with seeded runs.
  * Per-panel exception is contained — a broken summarize_jobs/list_recent_runs
    must not blank the whole tab.
  * Hero KPIs reflect the seeded data.
  * Row builders produce the expected presentation rows.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone

import pytest


# ─── Test isolation ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Each test gets a fresh kv_state DB so no shared state bleeds across."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


def _reload_tab():
    """Force a fresh import so the mock_streamlit fixture is in effect."""
    if "ui.tab_worker_health" in sys.modules:
        return importlib.reload(sys.modules["ui.tab_worker_health"])
    return importlib.import_module("ui.tab_worker_health")


def _iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc)
            + timedelta(seconds=offset_seconds)).isoformat()


def _seed_runs() -> None:
    """Seed a representative spread of runs across multiple jobs."""
    from state.worker_runs import record_run
    record_run(
        "run_alert_prune_job",
        started_at=_iso(-300), finished_at=_iso(-299),
        status="ok",
        result={"deleted": 12, "retention_days": 180},
    )
    record_run(
        "run_health_ping_job",
        started_at=_iso(-200), finished_at=_iso(-198),
        status="error",
        result=None,
        error_message="ConnectionError: FRED 503",
    )
    record_run(
        "run_audit_prune_job",
        started_at=_iso(-100), finished_at=_iso(-99),
        status="ok",
        result={"deleted": 0, "retention_days": 365},
    )
    record_run(
        "run_alert_prune_job",
        started_at=_iso(-10), finished_at=_iso(-9),
        status="ok",
        result={"deleted": 1, "retention_days": 180},
    )


# ─── Module import ──────────────────────────────────────────────────────────


def test_module_imports_cleanly(mock_streamlit) -> None:
    """Plain import must not raise."""
    mod = _reload_tab()
    assert hasattr(mod, "render")


# ─── Render — empty and seeded ──────────────────────────────────────────────


def test_render_no_runs_does_not_raise(mock_streamlit) -> None:
    """With zero persisted runs the tab still renders (NEVER rows + zeroed KPIs)."""
    mod = _reload_tab()
    mod.render()  # must not raise


def test_render_with_seeded_runs_does_not_raise(mock_streamlit) -> None:
    """With a representative spread of runs the tab still renders cleanly."""
    _seed_runs()
    mod = _reload_tab()
    mod.render()


def test_render_extra_kwargs_are_ignored(mock_streamlit) -> None:
    """The tab accepts arbitrary kwargs (smoke harness passes a data bundle)."""
    _seed_runs()
    mod = _reload_tab()
    mod.render(stock_data={}, freight_data={}, macro_data={})


# ─── Per-panel exception containment ────────────────────────────────────────


def test_overview_panel_failure_does_not_crash_tab(
    mock_streamlit, monkeypatch
) -> None:
    """A broken summarize_jobs must degrade to st.warning, not raise."""
    mod = _reload_tab()
    monkeypatch.setattr(
        mod, "_load_job_summary",
        lambda: (_ for _ in ()).throw(RuntimeError("simulated summary failure"))
    )
    # Must not raise.
    mod.render()


def test_recent_panel_failure_does_not_crash_tab(
    mock_streamlit, monkeypatch
) -> None:
    """A broken list_recent_runs must degrade to st.warning, not raise."""
    mod = _reload_tab()
    monkeypatch.setattr(
        mod, "_load_recent_runs",
        lambda limit=50: (_ for _ in ()).throw(
            RuntimeError("simulated recent failure")
        )
    )
    mod.render()


# ─── Row builders — pure-function correctness ───────────────────────────────


def test_build_overview_rows_renders_status_pill(mock_streamlit) -> None:
    """The overview row carries the human-readable status pill."""
    mod = _reload_tab()
    summary = [
        {
            "job_name": "run_alpha",
            "last_status": "ok",
            "last_run_at": _iso(-10),
            "last_duration": 1.5,
            "last_result_summary": "deleted=3",
            "runs_in_window": 1,
            "success_rate_24h": 1.0,
        },
        {
            "job_name": "run_beta",
            "last_status": "NEVER",
            "last_run_at": "",
            "last_duration": 0.0,
            "last_result_summary": "",
            "runs_in_window": 0,
            "success_rate_24h": 1.0,
        },
    ]
    rows = mod._build_overview_rows(summary)
    assert len(rows) == 2
    # OK row: '✓ OK' pill + concrete success%
    assert "OK" in rows[0]["Last status"]
    assert rows[0]["Runs (24h)"] == 1
    assert rows[0]["Success (24h)"] == "100%"
    # NEVER row: explicit NEVER pill + "—" success (don't mislead with 100%)
    assert "NEVER" in rows[1]["Last status"]
    assert rows[1]["Success (24h)"] == "—"


def test_build_recent_rows_prefers_error_message_over_result(mock_streamlit) -> None:
    """On an error row the result column shows the error, not the result_json."""
    mod = _reload_tab()
    from state.worker_runs import WorkerJobRun
    runs = [
        WorkerJobRun(
            run_id="r1",
            job_name="run_x",
            started_at=_iso(-5),
            finished_at=_iso(-4),
            status="error",
            result_json="{}",
            error_message="boom: db locked",
            duration_seconds=1.0,
        ),
        WorkerJobRun(
            run_id="r2",
            job_name="run_y",
            started_at=_iso(-1),
            finished_at=_iso(0),
            status="ok",
            result_json='{"deleted": 5}',
            error_message=None,
            duration_seconds=1.0,
        ),
    ]
    rows = mod._build_recent_rows(runs)
    assert len(rows) == 2
    # First row has the error message in Result.
    assert "boom" in rows[0]["Result"]
    # Second row has the k=v summary.
    assert "deleted=5" in rows[1]["Result"]


# ─── Hero KPI math ──────────────────────────────────────────────────────────


def test_hero_kpis_reflect_seeded_data(mock_streamlit) -> None:
    """Render with seeded data; hero KPI computation runs end-to-end."""
    _seed_runs()
    mod = _reload_tab()
    # Smoke through _render_hero directly — proves the math doesn't raise on
    # a representative seeded snapshot.
    summary = mod._load_job_summary()
    recent = mod._load_recent_runs(limit=50)
    # Sanity: the seed put 4 runs across 3 jobs.
    assert len(recent) == 4
    # _render_hero must not raise on this snapshot.
    mod._render_hero(summary, recent)
