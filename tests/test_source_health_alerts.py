"""Tests for engine.source_health_alerts — auto-fire shipping alerts on
data-source degradation.

Defining properties under test:
  - Healthy snapshot fires nothing.
  - Red (down) source persists one CRITICAL ShippingAlert with
    alert_type='SOURCE_HEALTH'.
  - Yellow (degraded) source persists one HIGH ShippingAlert.
  - Stale beyond red_threshold_minutes → CRITICAL even when status is
    'up'.
  - Stale between yellow and red → HIGH.
  - Cooldown blocks a re-fire for the same source within the window.
  - Cooldown is per-source — a cooldown on source A doesn't suppress
    an alert on source B.
  - Cooldown is per-user — alice's cooldown doesn't suppress bob.
  - enabled=False short-circuits before any work; no alerts are saved.
  - Source recovery (down → up) does NOT fire on the upward transition.
  - check_source_health_and_fire NEVER raises on a broken summary
    fetch — errored counter is incremented instead.
  - Config load round-trips via save_config → load_config.
  - Missing config row → defaults.
  - Worker integration: run_source_health_alert_job calls the helper
    and swallows engine errors.
  - main() invokes run_source_health_alert_job at the right position
    (after the prune jobs, before bulk-export prune).

Every test stubs ``engine.source_health.get_health_summary`` via
monkeypatch — no test ever hits the SQLite probe table.
"""
from __future__ import annotations

import sys
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

def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _summary(by_source: dict) -> dict:
    """Build a get_health_summary-shaped dict from a per-source mapping."""
    return {
        "window_hours":    24,
        "total_pings":     sum(int(s.get("count", 0) or 0) for s in by_source.values()),
        "by_source":       by_source,
        "current_outages": sorted(
            src for src, s in by_source.items() if s.get("last_status") == "down"
        ),
    }


def _patch_summary(monkeypatch, summary: dict) -> None:
    """Force engine.source_health.get_health_summary to return ``summary``."""
    from engine import source_health

    monkeypatch.setattr(
        source_health, "get_health_summary", lambda window_hours=24: summary
    )


def _fresh_up_stats(now: datetime, source: str = "fred") -> dict:
    return {
        "count":            10,
        "up_count":         10,
        "degraded_count":   0,
        "down_count":       0,
        "avg_duration_ms":  100.0,
        "last_status":      "up",
        "last_started_at":  _iso(now - timedelta(minutes=1)),
    }


def _down_stats(now: datetime) -> dict:
    return {
        "count":            10,
        "up_count":         5,
        "degraded_count":   0,
        "down_count":       5,
        "avg_duration_ms":  200.0,
        "last_status":      "down",
        "last_started_at":  _iso(now - timedelta(minutes=2)),
    }


def _degraded_stats(now: datetime) -> dict:
    return {
        "count":            10,
        "up_count":         5,
        "degraded_count":   5,
        "down_count":       0,
        "avg_duration_ms":  300.0,
        "last_status":      "degraded",
        "last_started_at":  _iso(now - timedelta(minutes=2)),
    }


# ─── Defining properties ──────────────────────────────────────────────────

def test_healthy_snapshot_fires_nothing(monkeypatch) -> None:
    """When every source's latest ping is fresh & up, no alerts fire."""
    from engine.source_health_alerts import check_source_health_and_fire
    from engine.alert_engine_v2 import load_alerts

    now = datetime.now(timezone.utc)
    _patch_summary(
        monkeypatch,
        _summary({
            "fred":     _fresh_up_stats(now, "fred"),
            "yfinance": _fresh_up_stats(now, "yfinance"),
        }),
    )

    counts = check_source_health_and_fire(user_id="alice")

    assert counts == {"fired": 0, "skipped_cooldown": 0, "errored": 0}
    assert load_alerts(user_id="alice") == []


def test_red_source_fires_one_critical(monkeypatch) -> None:
    """A 'down' source persists exactly one CRITICAL ShippingAlert with
    alert_type='SOURCE_HEALTH'."""
    from engine.source_health_alerts import check_source_health_and_fire
    from engine.alert_engine_v2 import load_alerts

    now = datetime.now(timezone.utc)
    _patch_summary(monkeypatch, _summary({"fred": _down_stats(now)}))

    counts = check_source_health_and_fire(user_id="alice")

    assert counts["fired"] == 1
    assert counts["errored"] == 0

    alerts = load_alerts(user_id="alice")
    assert len(alerts) == 1
    a = alerts[0]
    assert a.alert_type == "SOURCE_HEALTH"
    assert a.severity == "CRITICAL"
    assert "fred" in a.title.lower()


def test_yellow_source_fires_one_high(monkeypatch) -> None:
    """A 'degraded' source persists a HIGH ShippingAlert."""
    from engine.source_health_alerts import check_source_health_and_fire
    from engine.alert_engine_v2 import load_alerts

    now = datetime.now(timezone.utc)
    _patch_summary(monkeypatch, _summary({"yfinance": _degraded_stats(now)}))

    counts = check_source_health_and_fire(user_id="alice")

    assert counts["fired"] == 1
    alerts = load_alerts(user_id="alice")
    assert len(alerts) == 1
    assert alerts[0].severity == "HIGH"
    assert alerts[0].alert_type == "SOURCE_HEALTH"


def test_stale_up_source_above_red_threshold_fires_critical(monkeypatch) -> None:
    """An 'up' source whose last_started_at is older than the red
    threshold escalates to CRITICAL — the freshness of the ping is the
    signal, not the boolean status."""
    from engine.source_health_alerts import SourceHealthAlertConfig, check_source_health_and_fire
    from engine.alert_engine_v2 import load_alerts

    now = datetime.now(timezone.utc)
    stats = _fresh_up_stats(now)
    stats["last_started_at"] = _iso(now - timedelta(minutes=90))  # > 60min red
    _patch_summary(monkeypatch, _summary({"oecd": stats}))

    cfg = SourceHealthAlertConfig(
        enabled=True, red_threshold_minutes=60, yellow_threshold_minutes=30,
        cooldown_minutes=120,
    )
    counts = check_source_health_and_fire(cfg, user_id="alice")

    assert counts["fired"] == 1
    alerts = load_alerts(user_id="alice")
    assert alerts[0].severity == "CRITICAL"


def test_stale_up_source_between_yellow_and_red_fires_high(monkeypatch) -> None:
    """An 'up' source that's stale enough to cross yellow but under red
    fires HIGH."""
    from engine.source_health_alerts import SourceHealthAlertConfig, check_source_health_and_fire
    from engine.alert_engine_v2 import load_alerts

    now = datetime.now(timezone.utc)
    stats = _fresh_up_stats(now)
    stats["last_started_at"] = _iso(now - timedelta(minutes=45))  # > 30 yellow, < 60 red
    _patch_summary(monkeypatch, _summary({"imf": stats}))

    cfg = SourceHealthAlertConfig(
        enabled=True, red_threshold_minutes=60, yellow_threshold_minutes=30,
        cooldown_minutes=120,
    )
    counts = check_source_health_and_fire(cfg, user_id="alice")

    assert counts["fired"] == 1
    alerts = load_alerts(user_id="alice")
    assert alerts[0].severity == "HIGH"


def test_cooldown_blocks_refire_within_window(monkeypatch) -> None:
    """Two passes with the same red source one minute apart: only the
    first fires; the second is counted as skipped_cooldown."""
    from engine.source_health_alerts import check_source_health_and_fire

    now = datetime.now(timezone.utc)
    _patch_summary(monkeypatch, _summary({"fred": _down_stats(now)}))

    first = check_source_health_and_fire(user_id="alice")
    second = check_source_health_and_fire(user_id="alice")

    assert first == {"fired": 1, "skipped_cooldown": 0, "errored": 0}
    assert second == {"fired": 0, "skipped_cooldown": 1, "errored": 0}


def test_cooldown_is_per_source(monkeypatch) -> None:
    """A cooldown on source A does NOT block an alert on source B."""
    from engine.source_health_alerts import check_source_health_and_fire

    now = datetime.now(timezone.utc)
    # First pass — only A is red.
    _patch_summary(monkeypatch, _summary({"fred": _down_stats(now)}))
    first = check_source_health_and_fire(user_id="alice")
    assert first["fired"] == 1

    # Second pass — A still red (cooldown blocks), B newly red (must fire).
    _patch_summary(
        monkeypatch,
        _summary({
            "fred":     _down_stats(now),
            "yfinance": _down_stats(now),
        }),
    )
    second = check_source_health_and_fire(user_id="alice")
    assert second["fired"] == 1
    assert second["skipped_cooldown"] == 1


def test_cooldown_is_per_user(monkeypatch) -> None:
    """Alice's cooldown on a source does NOT block Bob's alert on the
    same source."""
    from engine.source_health_alerts import check_source_health_and_fire

    now = datetime.now(timezone.utc)
    _patch_summary(monkeypatch, _summary({"fred": _down_stats(now)}))

    a_first = check_source_health_and_fire(user_id="alice")
    a_second = check_source_health_and_fire(user_id="alice")
    b_first = check_source_health_and_fire(user_id="bob")

    assert a_first["fired"] == 1
    assert a_second["skipped_cooldown"] == 1
    assert b_first["fired"] == 1  # bob is unaffected by alice's cooldown


def test_enabled_false_short_circuits(monkeypatch) -> None:
    """When config.enabled is False, no alerts are saved even though the
    snapshot has red sources."""
    from engine.source_health_alerts import SourceHealthAlertConfig, check_source_health_and_fire
    from engine.alert_engine_v2 import load_alerts

    now = datetime.now(timezone.utc)
    _patch_summary(monkeypatch, _summary({"fred": _down_stats(now)}))

    cfg = SourceHealthAlertConfig(enabled=False)
    counts = check_source_health_and_fire(cfg, user_id="alice")

    assert counts == {"fired": 0, "skipped_cooldown": 0, "errored": 0}
    assert load_alerts(user_id="alice") == []


def test_recovery_does_not_fire(monkeypatch) -> None:
    """Source going red → yellow → green over three passes never fires
    on the upward transition (only on the degradations, gated by
    cooldown)."""
    from engine.source_health_alerts import SourceHealthAlertConfig, check_source_health_and_fire

    now = datetime.now(timezone.utc)
    # Short cooldown so the second pass is allowed to fire if it wants to.
    cfg = SourceHealthAlertConfig(
        enabled=True, red_threshold_minutes=60, yellow_threshold_minutes=30,
        cooldown_minutes=0,
    )

    # Pass 1: red.
    _patch_summary(monkeypatch, _summary({"fred": _down_stats(now)}))
    p1 = check_source_health_and_fire(cfg, user_id="alice")
    assert p1["fired"] == 1

    # Pass 2: source recovered to fresh-up.
    _patch_summary(monkeypatch, _summary({"fred": _fresh_up_stats(now, "fred")}))
    p2 = check_source_health_and_fire(cfg, user_id="alice")
    assert p2 == {"fired": 0, "skipped_cooldown": 0, "errored": 0}

    # Pass 3: still healthy.
    p3 = check_source_health_and_fire(cfg, user_id="alice")
    assert p3 == {"fired": 0, "skipped_cooldown": 0, "errored": 0}


def test_check_never_raises_on_broken_summary(monkeypatch) -> None:
    """If get_health_summary raises, the orchestrator returns the
    errored count instead of propagating."""
    from engine import source_health
    from engine.source_health_alerts import check_source_health_and_fire

    def _broken(window_hours: int = 24):
        raise RuntimeError("DB exploded")

    monkeypatch.setattr(source_health, "get_health_summary", _broken)

    counts = check_source_health_and_fire(user_id="alice")

    assert counts == {"fired": 0, "skipped_cooldown": 0, "errored": 1}


def test_per_source_error_does_not_kill_loop(monkeypatch) -> None:
    """A single malformed source entry must NOT stop the rest of the
    loop. We force one source's stats to a non-dict; the loop should
    increment errored and continue to the next source."""
    from engine.source_health_alerts import check_source_health_and_fire

    now = datetime.now(timezone.utc)
    summary = {
        "window_hours":    24,
        "total_pings":     20,
        "by_source": {
            "fred":  _down_stats(now),
            "broke": "not-a-dict",  # forces a crash inside classify/build
            "yfin":  _degraded_stats(now),
        },
        "current_outages": ["fred"],
    }
    _patch_summary(monkeypatch, summary)

    counts = check_source_health_and_fire(user_id="alice")

    # 'broke' source classifies as None (not a dict → returns None) so
    # neither fires nor errors. The two real sources still fire.
    assert counts["fired"] == 2
    assert counts["errored"] == 0


def test_config_load_missing_returns_defaults() -> None:
    """An empty kv_state row returns a SourceHealthAlertConfig with the
    documented defaults."""
    from engine.source_health_alerts import SourceHealthAlertConfig, load_config

    cfg = load_config(user_id="alice")

    defaults = SourceHealthAlertConfig()
    assert cfg.enabled == defaults.enabled
    assert cfg.red_threshold_minutes == defaults.red_threshold_minutes
    assert cfg.yellow_threshold_minutes == defaults.yellow_threshold_minutes
    assert cfg.cooldown_minutes == defaults.cooldown_minutes


def test_config_save_and_reload_round_trip() -> None:
    """A custom config persists across save_config → load_config."""
    from engine.source_health_alerts import SourceHealthAlertConfig, load_config, save_config

    cfg = SourceHealthAlertConfig(
        enabled=False,
        red_threshold_minutes=15,
        yellow_threshold_minutes=5,
        cooldown_minutes=33,
    )
    assert save_config(cfg, user_id="alice") is True

    reloaded = load_config(user_id="alice")
    assert reloaded.enabled is False
    assert reloaded.red_threshold_minutes == 15
    assert reloaded.yellow_threshold_minutes == 5
    assert reloaded.cooldown_minutes == 33


def test_config_yellow_clamped_below_red_on_load() -> None:
    """A persisted yellow >= red is clamped to red - 1 on load so the
    severity comparison stays well-ordered."""
    from engine.source_health_alerts import SourceHealthAlertConfig, load_config, save_config

    cfg = SourceHealthAlertConfig(
        enabled=True,
        red_threshold_minutes=30,
        yellow_threshold_minutes=60,  # inverted
        cooldown_minutes=120,
    )
    save_config(cfg, user_id="alice")
    reloaded = load_config(user_id="alice")
    assert reloaded.red_threshold_minutes == 30
    assert reloaded.yellow_threshold_minutes < reloaded.red_threshold_minutes


def test_run_source_health_alert_job_invokes_engine(monkeypatch) -> None:
    """The worker wrapper calls check_source_health_and_fire and returns
    the count dict."""
    from worker import scheduler

    mock = MagicMock(return_value={"fired": 3, "skipped_cooldown": 1, "errored": 0})
    monkeypatch.setattr(
        "engine.source_health_alerts.check_source_health_and_fire", mock
    )

    result = scheduler.run_source_health_alert_job()

    assert result == {"fired": 3, "skipped_cooldown": 1, "errored": 0}
    mock.assert_called_once()


def test_run_source_health_alert_job_swallows_engine_errors(monkeypatch) -> None:
    """If the orchestrator itself raises, the wrapper returns zeros."""
    from worker import scheduler

    def _broken(**kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(
        "engine.source_health_alerts.check_source_health_and_fire", _broken
    )

    result = scheduler.run_source_health_alert_job()

    assert result == {"fired": 0, "skipped_cooldown": 0, "errored": 0}


def test_main_calls_source_health_alert_job_after_health_prune(monkeypatch) -> None:
    """main() invokes run_source_health_alert_job AFTER the health
    ping + prune and BEFORE the bulk-export prune."""
    from worker import scheduler
    from worker.scheduler import ReportJobResult, main

    call_order: list[str] = []

    monkeypatch.setattr(scheduler, "load_data_bundle", lambda: {})
    monkeypatch.setattr(
        scheduler,
        "run_daily_briefing_job",
        lambda bundle, *, push_to_channels=False: (
            call_order.append("briefing") or ReportJobResult(
                report_id="rid", file_path="/tmp/x.html", success=True,
                duration_s=0.0, error_msg="",
            )
        ),
    )
    monkeypatch.setattr(
        scheduler, "run_telemetry_prune_job",
        lambda *a, **k: call_order.append("llm_prune") or 0,
    )
    monkeypatch.setattr(
        scheduler, "run_perf_prune_job",
        lambda *a, **k: call_order.append("perf_prune") or 0,
    )
    monkeypatch.setattr(
        scheduler, "run_snapshot_prune_job",
        lambda *a, **k: call_order.append("snap_prune") or 0,
    )
    monkeypatch.setattr(
        scheduler, "run_health_ping_job",
        lambda *a, **k: call_order.append("health_ping") or [],
    )
    monkeypatch.setattr(
        scheduler, "run_health_prune_job",
        lambda *a, **k: call_order.append("health_prune") or 0,
    )
    monkeypatch.setattr(
        scheduler, "run_source_health_alert_job",
        lambda *a, **k: call_order.append("source_health_alert") or {},
    )
    monkeypatch.setattr(
        scheduler, "run_bulk_export_prune_job",
        lambda *a, **k: call_order.append("bulk_prune") or 0,
    )
    monkeypatch.setattr(sys, "argv", ["worker.scheduler"])

    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0

    # source_health_alert must follow health_ping + health_prune and
    # precede bulk_prune.
    idx_alert = call_order.index("source_health_alert")
    assert call_order.index("health_ping") < idx_alert
    assert call_order.index("health_prune") < idx_alert
    assert idx_alert < call_order.index("bulk_prune")


# ─── Recent-fire counter ──────────────────────────────────────────────────

def test_recent_fire_counter_increments_on_fire(monkeypatch) -> None:
    """Every successful fire bumps the rolling 'recent fires in the
    last hour' counter that the UI surface uses."""
    from engine.source_health_alerts import (
        check_source_health_and_fire, get_recent_fire_count,
    )

    now = datetime.now(timezone.utc)
    _patch_summary(
        monkeypatch,
        _summary({
            "fred":     _down_stats(now),
            "yfinance": _degraded_stats(now),
        }),
    )

    assert get_recent_fire_count(user_id="alice") == 0
    check_source_health_and_fire(user_id="alice")
    assert get_recent_fire_count(user_id="alice") == 2
