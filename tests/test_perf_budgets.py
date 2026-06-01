"""Tests for engine.perf_budgets — per-tab render-latency budgets.

Defining properties under test:
  - get_default_budgets returns a non-empty list of PerfBudget rows.
  - load_budgets without customisation returns defaults.
  - save_budgets + load_budgets round-trips correctly per-user.
  - save_budgets([]) writes an empty-list marker that load reads as
    "use defaults" — the explicit reset path.
  - check_budgets returns [] when no render events exist (no data,
    no breach).
  - check_budgets returns [] when observed p95 is within budget.
  - check_budgets emits a 'warn' BudgetBreach when observed_p95 is
    1.5x the budget.
  - check_budgets emits a 'critical' BudgetBreach when observed_p95
    is 3x the budget.
  - check_budgets is per-user — alice's budgets don't leak into bob's
    check.
  - check_budgets skips tabs with < 5 samples (min-sample threshold).
  - check_and_alert fires PERF_BUDGET ShippingAlerts for breaches.
  - check_and_alert cooldown suppresses re-alerts within the window.
  - check_and_alert returns the count dict.
  - check_and_alert NEVER raises at the top level.
  - run_perf_budget_check_job wraps check_and_alert and returns its
    dict.
  - run_perf_budget_check_job swallows engine errors.
  - Severity classification at the 1x / 2x boundaries.

Every test patches DB_PATH to tmp_path so no test touches the real
SQLite file. Render events are written via the public record_render
API so the perf summary the budget check consumes is real.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


# ─── Per-test SQLite isolation ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Fresh DB at tmp_path so no test touches cache/ship_tracker.db."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ──────────────────────────────────────────────────────────────

def _seed_render_events(tab_name: str, durations_ms: list[int]) -> None:
    """Insert one render event per duration via the public API."""
    from engine.perf_telemetry import record_render

    for d in durations_ms:
        record_render(tab_name=tab_name, duration_ms=int(d), success=True)


# ─── Defaults ─────────────────────────────────────────────────────────────

def test_get_default_budgets_returns_non_empty() -> None:
    """The shipped defaults must cover at least the headline tabs."""
    from engine.perf_budgets import get_default_budgets

    defaults = get_default_budgets()
    assert isinstance(defaults, list)
    assert len(defaults) > 0
    tabs = {b.tab_module for b in defaults}
    # Spot-check a couple of headline tabs — exact list is implementation
    # detail but these are the ones the project memory references.
    assert "ui.tab_overview" in tabs
    assert "ui.tab_alerts" in tabs
    assert "ui.tab_deep_dive" in tabs


def test_get_default_budgets_returns_fresh_list_each_call() -> None:
    """Mutating one call's result must not poison the next."""
    from engine.perf_budgets import get_default_budgets

    first = get_default_budgets()
    first.pop()
    second = get_default_budgets()
    assert len(second) > len(first)


# ─── load / save round-trip ───────────────────────────────────────────────

def test_load_budgets_without_customisation_returns_defaults() -> None:
    """No saved row → defaults."""
    from engine.perf_budgets import load_budgets, get_default_budgets

    loaded = load_budgets(user_id="alice")
    defaults = get_default_budgets()
    assert len(loaded) == len(defaults)
    assert {b.tab_module for b in loaded} == {b.tab_module for b in defaults}


def test_save_and_reload_round_trip() -> None:
    """save → load returns the exact same custom budgets."""
    from engine.perf_budgets import save_budgets, load_budgets, PerfBudget

    custom = [
        PerfBudget(tab_module="ui.tab_x", max_p95_seconds=1.0),
        PerfBudget(tab_module="ui.tab_y", max_p95_seconds=3.5,
                   max_mean_seconds=1.5, window_hours=6),
    ]
    assert save_budgets(custom, user_id="alice") is True

    reloaded = load_budgets(user_id="alice")
    assert len(reloaded) == 2
    by_tab = {b.tab_module: b for b in reloaded}
    assert by_tab["ui.tab_x"].max_p95_seconds == 1.0
    assert by_tab["ui.tab_y"].max_p95_seconds == 3.5
    assert by_tab["ui.tab_y"].max_mean_seconds == 1.5
    assert by_tab["ui.tab_y"].window_hours == 6


def test_save_empty_list_reverts_to_defaults() -> None:
    """save_budgets([]) is the explicit reset — load returns defaults."""
    from engine.perf_budgets import save_budgets, load_budgets, get_default_budgets, PerfBudget

    # First customise.
    save_budgets([PerfBudget(tab_module="ui.tab_x", max_p95_seconds=1.0)],
                 user_id="alice")
    # Reset.
    assert save_budgets([], user_id="alice") is True
    reloaded = load_budgets(user_id="alice")
    assert len(reloaded) == len(get_default_budgets())


def test_save_per_user_scoping() -> None:
    """Alice's saves don't leak into bob's reads."""
    from engine.perf_budgets import save_budgets, load_budgets, PerfBudget, get_default_budgets

    save_budgets([PerfBudget(tab_module="ui.tab_x", max_p95_seconds=0.5)],
                 user_id="alice")
    bob_budgets = load_budgets(user_id="bob")
    bob_tabs = {b.tab_module for b in bob_budgets}
    assert "ui.tab_x" not in bob_tabs or len(bob_budgets) == len(get_default_budgets())


# ─── check_budgets ────────────────────────────────────────────────────────

def test_check_budgets_no_events_returns_empty() -> None:
    """No render rows in the window → no breaches (no data, no breach)."""
    from engine.perf_budgets import check_budgets

    breaches = check_budgets(user_id="alice")
    assert breaches == []


def test_check_budgets_within_budget_returns_empty() -> None:
    """Events with p95 below budget → no breach."""
    from engine.perf_budgets import check_budgets, save_budgets, PerfBudget

    save_budgets(
        [PerfBudget(tab_module="ui.tab_x", max_p95_seconds=5.0)],
        user_id="alice",
    )
    # 10 renders all around 100ms — well under 5s budget.
    _seed_render_events("ui.tab_x", [100] * 10)

    breaches = check_budgets(user_id="alice")
    assert breaches == []


def test_check_budgets_15x_over_emits_warn() -> None:
    """observed_p95 ≈ 1.5x budget → severity='warn'."""
    from engine.perf_budgets import check_budgets, save_budgets, PerfBudget

    save_budgets(
        [PerfBudget(tab_module="ui.tab_x", max_p95_seconds=1.0)],
        user_id="alice",
    )
    # 10 renders at 1500ms (1.5s). budget=1.0s, p95 ≈ 1.5s = 1.5x.
    _seed_render_events("ui.tab_x", [1500] * 10)

    breaches = check_budgets(user_id="alice")
    assert len(breaches) == 1
    breach = breaches[0]
    assert breach.tab_module == "ui.tab_x"
    assert breach.severity == "warn"
    assert breach.budget_p95 == 1.0
    assert breach.observed_p95 == 1.5
    assert breach.sample_count == 10


def test_check_budgets_3x_over_emits_critical() -> None:
    """observed_p95 ≈ 3x budget → severity='critical'."""
    from engine.perf_budgets import check_budgets, save_budgets, PerfBudget

    save_budgets(
        [PerfBudget(tab_module="ui.tab_x", max_p95_seconds=1.0)],
        user_id="alice",
    )
    # 10 renders at 3000ms (3s) — 3x the 1s budget.
    _seed_render_events("ui.tab_x", [3000] * 10)

    breaches = check_budgets(user_id="alice")
    assert len(breaches) == 1
    assert breaches[0].severity == "critical"


def test_check_budgets_per_user_scoping() -> None:
    """alice's events / budgets don't bleed into bob's check.

    Render events are GLOBAL (not per-user) — the perf_telemetry table
    has no user_id column — so the per-user property under test is
    that custom BUDGETS are per-user, even though the underlying
    rendering data is shared. We verify by giving alice a tight
    budget and bob the defaults; alice should breach, bob should not
    (the default ui.tab_x budget doesn't exist).
    """
    from engine.perf_budgets import check_budgets, save_budgets, PerfBudget

    save_budgets(
        [PerfBudget(tab_module="ui.tab_x", max_p95_seconds=1.0)],
        user_id="alice",
    )
    _seed_render_events("ui.tab_x", [3000] * 10)

    alice_breaches = check_budgets(user_id="alice")
    bob_breaches = check_budgets(user_id="bob")

    assert len(alice_breaches) == 1
    assert alice_breaches[0].tab_module == "ui.tab_x"
    # bob's default budgets don't include ui.tab_x → no breach for him.
    assert all(b.tab_module != "ui.tab_x" for b in bob_breaches)


def test_check_budgets_skips_below_min_samples() -> None:
    """A tab with fewer than _MIN_SAMPLES (5) observations is skipped."""
    from engine.perf_budgets import check_budgets, save_budgets, PerfBudget

    save_budgets(
        [PerfBudget(tab_module="ui.tab_x", max_p95_seconds=0.1)],
        user_id="alice",
    )
    # Only 3 renders, all way over the 0.1s budget — but below the
    # min-sample threshold of 5, so no breach.
    _seed_render_events("ui.tab_x", [5000, 5000, 5000])

    breaches = check_budgets(user_id="alice")
    assert breaches == []


def test_check_budgets_at_min_samples_fires() -> None:
    """Exactly _MIN_SAMPLES (5) observations is enough to fire."""
    from engine.perf_budgets import check_budgets, save_budgets, PerfBudget

    save_budgets(
        [PerfBudget(tab_module="ui.tab_x", max_p95_seconds=0.1)],
        user_id="alice",
    )
    _seed_render_events("ui.tab_x", [5000] * 5)

    breaches = check_budgets(user_id="alice")
    assert len(breaches) == 1


# ─── Severity boundaries ──────────────────────────────────────────────────

def test_classify_severity_at_2x_boundary_is_warn() -> None:
    """Exactly 2x → warn (the boundary is strict: ratio > 2.0)."""
    from engine.perf_budgets import _classify_severity

    # 2.0 / 1.0 = 2.0 → NOT > 2.0 → warn
    assert _classify_severity(2.0, 1.0) == "warn"


def test_classify_severity_just_over_2x_is_critical() -> None:
    """Anything strictly greater than 2x → critical."""
    from engine.perf_budgets import _classify_severity

    assert _classify_severity(2.01, 1.0) == "critical"


def test_classify_severity_just_over_budget_is_warn() -> None:
    """A whisker over budget but well under 2x → warn."""
    from engine.perf_budgets import _classify_severity

    assert _classify_severity(1.1, 1.0) == "warn"


# ─── check_and_alert ──────────────────────────────────────────────────────

def test_check_and_alert_fires_perf_budget_alerts() -> None:
    """A breach is persisted as a PERF_BUDGET ShippingAlert."""
    from engine.perf_budgets import check_and_alert, save_budgets, PerfBudget
    from engine.alert_engine_v2 import load_alerts

    save_budgets(
        [PerfBudget(tab_module="ui.tab_x", max_p95_seconds=1.0)],
        user_id="alice",
    )
    _seed_render_events("ui.tab_x", [3000] * 10)

    counts = check_and_alert(user_id="alice")
    assert counts["breached"] == 1
    assert counts["alerted"] == 1
    assert counts["skipped_cooldown"] == 0

    alerts = load_alerts(user_id="alice")
    assert len(alerts) == 1
    a = alerts[0]
    assert a.alert_type == "PERF_BUDGET"
    assert a.severity == "HIGH"  # critical breach maps to HIGH ShippingAlert
    assert "ui.tab_x" in a.title


def test_check_and_alert_warn_breach_is_medium() -> None:
    """A 'warn' breach (1.5x) maps to a MEDIUM ShippingAlert."""
    from engine.perf_budgets import check_and_alert, save_budgets, PerfBudget
    from engine.alert_engine_v2 import load_alerts

    save_budgets(
        [PerfBudget(tab_module="ui.tab_y", max_p95_seconds=1.0)],
        user_id="alice",
    )
    _seed_render_events("ui.tab_y", [1500] * 10)

    counts = check_and_alert(user_id="alice")
    assert counts["alerted"] == 1
    alerts = load_alerts(user_id="alice")
    assert len(alerts) == 1
    assert alerts[0].severity == "MEDIUM"


def test_check_and_alert_cooldown_blocks_refire() -> None:
    """Second check inside the window does NOT re-alert."""
    from engine.perf_budgets import check_and_alert, save_budgets, PerfBudget

    save_budgets(
        [PerfBudget(tab_module="ui.tab_x", max_p95_seconds=1.0, window_hours=24)],
        user_id="alice",
    )
    _seed_render_events("ui.tab_x", [3000] * 10)

    first = check_and_alert(user_id="alice")
    second = check_and_alert(user_id="alice")

    assert first["alerted"] == 1
    assert second["alerted"] == 0
    assert second["skipped_cooldown"] == 1


def test_check_and_alert_count_dict_shape() -> None:
    """The return dict has exactly the four expected keys."""
    from engine.perf_budgets import check_and_alert

    counts = check_and_alert(user_id="alice")
    assert set(counts.keys()) == {"checked", "breached", "alerted", "skipped_cooldown"}


def test_check_and_alert_never_raises(monkeypatch) -> None:
    """Top-level exceptions are swallowed; a count dict is always
    returned even when load_budgets blows up."""
    from engine import perf_budgets

    def _broken(**kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(perf_budgets, "load_budgets", _broken)

    counts = perf_budgets.check_and_alert(user_id="alice")
    assert isinstance(counts, dict)
    assert set(counts.keys()) == {"checked", "breached", "alerted", "skipped_cooldown"}


def test_check_and_alert_no_breaches_no_alerts() -> None:
    """When nothing is in breach, alerted is 0 and no rows are written."""
    from engine.perf_budgets import check_and_alert, save_budgets, PerfBudget
    from engine.alert_engine_v2 import load_alerts

    save_budgets(
        [PerfBudget(tab_module="ui.tab_x", max_p95_seconds=10.0)],
        user_id="alice",
    )
    _seed_render_events("ui.tab_x", [100] * 10)

    counts = check_and_alert(user_id="alice")
    assert counts["breached"] == 0
    assert counts["alerted"] == 0
    assert load_alerts(user_id="alice") == []


# ─── Worker wrapper ───────────────────────────────────────────────────────

def test_run_perf_budget_check_job_invokes_engine(monkeypatch) -> None:
    """The worker wrapper calls check_and_alert and returns its dict."""
    from worker import scheduler

    mock = MagicMock(return_value={
        "checked": 5, "breached": 2, "alerted": 1, "skipped_cooldown": 1,
    })
    monkeypatch.setattr("engine.perf_budgets.check_and_alert", mock)

    result = scheduler.run_perf_budget_check_job()
    assert result == {
        "checked": 5, "breached": 2, "alerted": 1, "skipped_cooldown": 1,
    }
    mock.assert_called_once()


def test_run_perf_budget_check_job_swallows_engine_errors(monkeypatch) -> None:
    """If the orchestrator raises, the wrapper returns a zero dict."""
    from worker import scheduler

    def _broken(**kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr("engine.perf_budgets.check_and_alert", _broken)

    result = scheduler.run_perf_budget_check_job()
    assert result == {
        "checked": 0, "breached": 0, "alerted": 0, "skipped_cooldown": 0,
    }
