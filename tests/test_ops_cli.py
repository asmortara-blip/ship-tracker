"""Tests for ``tools.ops_cli`` — the operator CLI.

Each subcommand handler is exercised by calling ``main(argv)`` with a
synthetic ``argv`` and capturing stdout / stderr. The CLI's defining
properties under test:

* Every subcommand returns exit 0 on the happy path and prints SOMETHING
  to stdout (so a calling shell can `tee` the output safely).
* ``--json`` produces JSON the stdlib can round-trip (``json.loads``
  succeeds; nested datetimes / dataclasses survive via ``default=str``).
* Argparse rejection (unknown subcommand, missing required flag) yields
  exit 2 — not 1, not a traceback. Tests pin this contract because the
  CLI's calling scripts use exit codes to decide whether to retry.
* A handler that internally raises is converted to exit 1 plus a
  single-line stderr message — ``tokens create`` with a bogus user_id
  is one example, ``users create`` with a duplicate username is
  another.
* The raw token printed by ``tokens create`` appears EXACTLY ONCE on
  stdout (security property — operator can copy-paste it from terminal
  without accidentally pasting two copies).

The per-test isolation fixture monkeypatches ``state.db.DB_PATH`` so
each test gets its own tmp-path SQLite file; without it the test
process would touch the real ``cache/ship_tracker.db``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


# ─── Isolation fixture ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite at tmp_path. Mirrors the pattern used everywhere
    else in the suite (see test_alert_analytics.py, test_api_tokens.py).
    """
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ──────────────────────────────────────────────────────────────

def _run(argv: list[str], capsys) -> tuple[int, str, str]:
    """Call ``main(argv)`` and return (exit_code, stdout, stderr).

    capsys is the pytest fixture that captures stream writes from the
    handlers we're testing. Each call is hermetic — capsys resets
    between captures."""
    from tools.ops_cli import main

    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _mk_alert_row(severity: str = "HIGH", ack: bool = False, idx: int = 0) -> None:
    """Insert one alert directly via save_alerts so the CLI sees it.

    Tests use this to populate the DB before exercising the list/ack
    code paths. ``idx`` makes the alert_id + ticker unique so the v14
    dedup window doesn't collapse multiple inserts in the same call.
    """
    from engine.alert_engine_v2 import ShippingAlert, save_alerts

    alert = ShippingAlert(
        alert_id=f"alert-{idx}",
        created_at=datetime.now(timezone.utc).isoformat(),
        alert_type="MACRO",
        severity=severity,
        title=f"title-{idx}",
        body=f"body-{idx}",
        ticker=f"TKR{idx}",
        route_id="",
        port_locode="",
        value=0.0,
        threshold=0.0,
        change_pct=0.0,
        acknowledged=ack,
    )
    save_alerts([alert])


# ─── status ───────────────────────────────────────────────────────────────

def test_status_prints_kv(capsys) -> None:
    """Status should run against an empty DB without crashing and
    print the headline fields."""
    code, out, _ = _run(["status"], capsys)
    assert code == 0
    assert "schema_version" in out
    assert "user_count" in out
    assert "alerts_30d" in out
    assert "channels" in out


def test_status_json_is_valid_json(capsys) -> None:
    """--json output must round-trip through json.loads with the
    headline keys present."""
    code, out, _ = _run(["status", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert isinstance(payload["schema_version"], int)
    assert "user_count" in payload
    assert "alerts_30d" in payload


# ─── alerts list / ack ────────────────────────────────────────────────────

def test_alerts_list_empty_db(capsys) -> None:
    """Empty DB → table prints "(no rows)" not a crash. Empty stdout
    would be ambiguous so the handler always prints something."""
    code, out, _ = _run(["alerts", "list"], capsys)
    assert code == 0
    assert out.strip() != ""


def test_alerts_list_with_rows(capsys) -> None:
    _mk_alert_row(severity="HIGH", idx=1)
    _mk_alert_row(severity="CRITICAL", idx=2)
    code, out, _ = _run(["alerts", "list"], capsys)
    assert code == 0
    # Table header columns are present
    assert "alert_id" in out
    assert "severity" in out


def test_alerts_list_severity_filter(capsys) -> None:
    _mk_alert_row(severity="HIGH", idx=1)
    _mk_alert_row(severity="LOW", idx=2)
    code, out, _ = _run(["alerts", "list", "--severity", "LOW"], capsys)
    assert code == 0
    # The HIGH alert title should not be in the filtered output
    assert "title-2" in out


def test_alerts_list_json(capsys) -> None:
    _mk_alert_row(severity="HIGH", idx=1)
    code, out, _ = _run(["alerts", "list", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert len(payload) >= 1
    assert payload[0]["severity"] == "HIGH"


def test_alerts_ack(capsys) -> None:
    _mk_alert_row(severity="HIGH", idx=1)
    code, out, _ = _run(["alerts", "ack", "alert-1"], capsys)
    assert code == 0
    assert "alert-1" in out


def test_alerts_ack_all_json(capsys) -> None:
    _mk_alert_row(severity="HIGH", idx=1)
    _mk_alert_row(severity="HIGH", idx=2)
    code, out, _ = _run(["alerts", "ack-all", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["unread_before"] >= 0
    assert payload["unread_after"] == 0


def test_alerts_metrics_json(capsys) -> None:
    """--json output for metrics carries the by_severity / by_day
    breakdowns plus the headline numbers."""
    _mk_alert_row(severity="HIGH", idx=1, ack=True)
    code, out, _ = _run(["alerts", "metrics", "--window", "30", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert "total_alerts" in payload
    assert "by_severity" in payload


# ─── channels ────────────────────────────────────────────────────────────

def test_channels_list_empty(capsys) -> None:
    code, out, _ = _run(["channels", "list"], capsys)
    assert code == 0
    assert out.strip() != ""


def test_channels_list_json_is_list(capsys) -> None:
    code, out, _ = _run(["channels", "list", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert isinstance(payload, list)


def test_channels_delete_noop_on_unknown(capsys) -> None:
    """delete_channel is silent on unknown ids by design; the CLI
    surfaces a success line because the underlying call didn't raise."""
    code, out, _ = _run(["channels", "delete", "nonexistent-id"], capsys)
    assert code == 0
    assert "nonexistent-id" in out


# ─── channels usage / reset-usage / set-budget (schema v25) ──────────────


def _mk_channel_row(channel_id: str, *, budget: int = 0, user_id: str = "") -> None:
    """Insert one delivery channel through save_channel so the CLI
    handler picks it up. Helper mirrors the in-suite convention used
    by ``_mk_alert_row``."""
    from engine.alert_delivery import DeliveryChannel, save_channel

    save_channel(
        DeliveryChannel(
            channel_id=channel_id,
            name=f"ch-{channel_id}",
            kind="slack",
            target="https://hooks.slack.com/services/T/B/X",
            severity_threshold="LOW",
            enabled=True,
            monthly_budget=budget,
        ),
        user_id=user_id,
    )


def test_channels_usage_empty(capsys) -> None:
    """No channels yet → handler prints "(no channels)" rather than a
    crash. Mirrors the channels-list empty-DB shape."""
    code, out, _ = _run(["channels", "usage", "--user-id", "alice"], capsys)
    assert code == 0
    assert out.strip() != ""


def test_channels_usage_json_returns_list(capsys) -> None:
    """--json output is a list of {channel_id, name, budget, usage, ...}
    dicts. Each save_channel row contributes exactly one entry."""
    _mk_channel_row("c-cli-1", budget=100, user_id="alice")
    code, out, _ = _run(
        ["channels", "usage", "--user-id", "alice", "--json"], capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["channel_id"] == "c-cli-1"
    assert payload[0]["budget"] == 100


def test_channels_reset_usage_zeroes_counter(capsys) -> None:
    """`channels reset-usage <id>` zeros the per-channel month counter
    so a follow-up `channels usage` reports zero."""
    from engine.alert_delivery import (
        get_channel_usage,
        increment_channel_usage,
    )

    _mk_channel_row("c-cli-reset", budget=50, user_id="alice")
    for _ in range(7):
        increment_channel_usage("c-cli-reset", user_id="alice")
    assert get_channel_usage("c-cli-reset", user_id="alice") == 7
    code, out, _ = _run(
        ["channels", "reset-usage", "c-cli-reset", "--user-id", "alice"],
        capsys,
    )
    assert code == 0
    assert "c-cli-reset" in out
    assert get_channel_usage("c-cli-reset", user_id="alice") == 0


def test_channels_set_budget_updates_row(capsys) -> None:
    """`channels set-budget <id> --budget N` mutates the persisted
    monthly_budget; a follow-up `channels list` (or load_channels)
    sees the new cap."""
    from engine.alert_delivery import load_channels

    _mk_channel_row("c-cli-set", budget=10, user_id="alice")
    code, out, _ = _run(
        [
            "channels", "set-budget", "c-cli-set",
            "--budget", "999",
            "--user-id", "alice",
        ],
        capsys,
    )
    assert code == 0
    assert "999" in out
    channels = load_channels(user_id="alice")
    by_id = {c.channel_id: c for c in channels}
    assert by_id["c-cli-set"].monthly_budget == 999


def test_channels_set_budget_unknown_channel_returns_nonzero(capsys) -> None:
    """A missing channel_id surfaces a clear "channel not found" + a
    non-zero exit code so a wrapping script can detect the failure."""
    code, out, _ = _run(
        [
            "channels", "set-budget", "no-such-id",
            "--budget", "100",
            "--user-id", "alice",
        ],
        capsys,
    )
    # SystemExit(1) becomes exit code 1 inside main().
    assert code != 0
    assert "no-such-id" in out


# ─── reports ─────────────────────────────────────────────────────────────

def test_reports_list_empty(capsys) -> None:
    code, out, _ = _run(["reports", "list"], capsys)
    assert code == 0
    assert out.strip() != ""


def test_reports_stats_json(capsys) -> None:
    code, out, _ = _run(["reports", "stats", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["total_reports"] == 0
    assert "sentiment_distribution" in payload


# ─── reports diff (new in this commit) ───────────────────────────────────

def _seed_diffable_report(
    *,
    report_id: str,
    user_id: str,
    sentiment_score: float,
    risk_level: str,
    sentiment_label: str = "MIXED",
    generated_at: str = "2026-05-22T12:00:00+00:00",
    monkeypatch=None,
    tmp_path=None,
) -> None:
    """Drop a report_history row + HTML file scoped to user_id, suitable
    for the diff handler to load. Mirrors the helper in
    test_utils_report_diff.py — kept local rather than imported across
    test files so the CLI test stays self-contained."""
    from pathlib import Path

    from state.db import get_connection
    from utils import report_history as rh

    if monkeypatch is not None and tmp_path is not None:
        monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")
    rh.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    file_path = Path(rh.REPORT_DIR) / f"r_{report_id[:8]}.html"
    file_path.write_text(f"<html>{report_id}</html>", encoding="utf-8")

    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO report_history
              (report_id, generated_at, report_date, sentiment_label,
               sentiment_score, risk_level, signal_count, data_quality,
               file_path, file_size_kb, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id, generated_at, "May 22, 2026", sentiment_label,
                sentiment_score, risk_level, 0, "FULL",
                str(file_path.resolve()), 0.5, user_id,
            ),
        )


def test_reports_diff_unknown_ids_exits_nonzero_with_stderr_message(
    monkeypatch, tmp_path, capsys,
) -> None:
    """`reports diff` with an unknown id must exit non-zero AND print
    a one-line message to STDERR — the CLI's "handler raised → exit 1"
    contract. The traceback must NOT bubble to the shell."""
    from utils import report_history as rh
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")

    code, _out, err = _run(
        ["reports", "diff", "no-a", "no-b", "--user-id", "alice"],
        capsys,
    )
    assert code != 0
    # Stderr should carry the one-line failure message (no Python
    # Traceback header).
    assert "unknown in scope" in err.lower() or "one or both" in err.lower()
    assert "Traceback" not in err


def test_reports_diff_happy_path_prints_markdown(
    monkeypatch, tmp_path, capsys,
) -> None:
    """Default format is Markdown — a populated diff between two
    reports for the same user must produce a non-empty payload that
    mentions both ids and at least one delta description."""
    _seed_diffable_report(
        report_id="aaa-bbb-ccc", user_id="alice",
        sentiment_score=0.10, risk_level="LOW",
        monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    _seed_diffable_report(
        report_id="ddd-eee-fff", user_id="alice",
        sentiment_score=0.45, risk_level="HIGH",
        generated_at="2026-05-23T12:00:00+00:00",
    )
    code, out, _ = _run(
        [
            "reports", "diff",
            "aaa-bbb-ccc", "ddd-eee-fff",
            "--user-id", "alice",
        ],
        capsys,
    )
    assert code == 0
    # Both ids must appear in the header line.
    assert "aaa-bbb-ccc" in out
    assert "ddd-eee-fff" in out
    # And the diff must surface the metadata-level changes.
    assert "Sentiment" in out or "sentiment" in out
    assert "Risk" in out or "risk" in out


def test_reports_diff_json_format_returns_parseable_payload(
    monkeypatch, tmp_path, capsys,
) -> None:
    """--format json must produce a payload that round-trips through
    json.loads with the documented top-level keys."""
    _seed_diffable_report(
        report_id="a1", user_id="alice",
        sentiment_score=0.10, risk_level="LOW",
        monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    _seed_diffable_report(
        report_id="b1", user_id="alice",
        sentiment_score=0.50, risk_level="HIGH",
        generated_at="2026-05-23T12:00:00+00:00",
    )
    code, out, _ = _run(
        [
            "reports", "diff", "a1", "b1",
            "--user-id", "alice", "--format", "json",
        ],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    # Documented top-level keys.
    assert set(payload.keys()) >= {
        "report_a_id", "report_b_id", "summary", "entries",
    }
    assert payload["report_a_id"] == "a1"
    assert payload["report_b_id"] == "b1"
    assert isinstance(payload["entries"], list)
    assert {"added", "removed", "changed"} <= set(payload["summary"].keys())


def test_reports_diff_per_user_scoping_blocks_cross_user(
    monkeypatch, tmp_path, capsys,
) -> None:
    """Alice cannot diff Bob's reports — the loader returns None for
    Bob's id in Alice's scope, and the CLI exits non-zero with the
    same "unknown in scope" message used for genuinely-missing ids.
    Same indistinguishability contract as the API's /reports/<id>/html
    cross-user path."""
    _seed_diffable_report(
        report_id="bob-1", user_id="bob",
        sentiment_score=0.10, risk_level="LOW",
        monkeypatch=monkeypatch, tmp_path=tmp_path,
    )
    _seed_diffable_report(
        report_id="bob-2", user_id="bob",
        sentiment_score=0.50, risk_level="HIGH",
    )
    code, _out, err = _run(
        [
            "reports", "diff", "bob-1", "bob-2",
            "--user-id", "alice",
        ],
        capsys,
    )
    assert code != 0
    assert "Traceback" not in err


# ─── telemetry ───────────────────────────────────────────────────────────

def test_telemetry_usage_empty(capsys) -> None:
    code, out, _ = _run(["telemetry", "usage"], capsys)
    assert code == 0
    assert "total_calls" in out


def test_telemetry_usage_json(capsys) -> None:
    code, out, _ = _run(["telemetry", "usage", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["total_calls"] == 0
    assert payload["window_days"] == 7


def test_telemetry_recent_empty(capsys) -> None:
    """Recent calls on an empty DB → ``(no rows)`` table line."""
    code, out, _ = _run(["telemetry", "recent"], capsys)
    assert code == 0
    assert out.strip() != ""


def test_telemetry_prune_returns_zero(capsys) -> None:
    """Prune on empty DB deletes zero rows; exit code is still 0."""
    code, out, _ = _run(["telemetry", "prune", "--retention", "90"], capsys)
    assert code == 0
    assert "deleted_rows" in out


# ─── perf ────────────────────────────────────────────────────────────────

def test_perf_summary_empty(capsys) -> None:
    code, out, _ = _run(["perf", "summary"], capsys)
    assert code == 0
    assert "total_renders" in out


# ─── health ──────────────────────────────────────────────────────────────

def test_health_summary_empty(capsys) -> None:
    code, out, _ = _run(["health", "summary"], capsys)
    assert code == 0
    assert "total_pings" in out


def test_health_summary_json(capsys) -> None:
    code, out, _ = _run(["health", "summary", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert "by_source" in payload


# ─── health-alerts ───────────────────────────────────────────────────────

def test_health_alerts_status_defaults(capsys) -> None:
    """`health-alerts status` reports the default config when no row
    has been saved yet."""
    code, out, _ = _run(["health-alerts", "status", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["enabled"] is True
    assert payload["red_threshold_minutes"] == 60
    assert payload["yellow_threshold_minutes"] == 30
    assert payload["cooldown_minutes"] == 120
    assert payload["recent_fires_last_hour"] == 0


def test_health_alerts_disable_then_status(capsys) -> None:
    """`disable` persists enabled=False; `status` reads it back."""
    code, _, _ = _run(["health-alerts", "disable", "--json"], capsys)
    assert code == 0
    code, out, _ = _run(["health-alerts", "status", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["enabled"] is False


def test_health_alerts_enable_then_status(capsys) -> None:
    """`enable` flips it back on after disable."""
    _run(["health-alerts", "disable"], capsys)
    code, _, _ = _run(["health-alerts", "enable", "--json"], capsys)
    assert code == 0
    code, out, _ = _run(["health-alerts", "status", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["enabled"] is True


def test_health_alerts_run_once_returns_count_dict(monkeypatch, capsys) -> None:
    """`run-once` invokes the orchestrator and prints the count dict."""
    from engine import source_health

    # Force one degraded source so the orchestrator fires.
    now_iso = datetime.now(timezone.utc).isoformat()
    summary = {
        "window_hours": 24,
        "total_pings":  1,
        "by_source": {
            "fred": {
                "count":           1,
                "up_count":        0,
                "degraded_count":  1,
                "down_count":      0,
                "avg_duration_ms": 100.0,
                "last_status":     "degraded",
                "last_started_at": now_iso,
            },
        },
        "current_outages": [],
    }
    monkeypatch.setattr(
        source_health, "get_health_summary", lambda window_hours=24: summary
    )

    code, out, _ = _run(["health-alerts", "run-once", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert set(payload.keys()) == {"fired", "skipped_cooldown", "errored"}
    assert payload["fired"] == 1


# ─── perf-budgets ────────────────────────────────────────────────────────

def test_perf_budgets_list_returns_defaults(capsys) -> None:
    """`perf-budgets list --json` returns the default budget set."""
    code, out, _ = _run(["perf-budgets", "list", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert len(payload) > 0
    # Every row carries the required keys.
    for row in payload:
        assert set(row.keys()) >= {
            "tab_module", "budget_p95", "observed_p95",
            "samples", "window_h", "status",
        }


def test_perf_budgets_list_text_table(capsys) -> None:
    """Plain (non-JSON) output renders an ASCII table with headers."""
    code, out, _ = _run(["perf-budgets", "list"], capsys)
    assert code == 0
    assert "tab_module" in out
    assert "budget_p95" in out
    assert "status" in out


def test_perf_budgets_set_then_list(capsys) -> None:
    """`set` upserts a budget; subsequent `list` shows the new value."""
    code, out, _ = _run(
        ["perf-budgets", "set", "ui.tab_overview", "--max-p95", "0.5", "--json"],
        capsys,
    )
    assert code == 0
    set_payload = json.loads(out)
    assert set_payload["tab_module"] == "ui.tab_overview"
    assert set_payload["max_p95_seconds"] == 0.5
    assert set_payload["saved"] is True

    code, out, _ = _run(["perf-budgets", "list", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    overview = next(p for p in payload if p["tab_module"] == "ui.tab_overview")
    assert overview["budget_p95"] == 0.5


def test_perf_budgets_reset_returns_defaults(capsys) -> None:
    """`reset` wipes customisations and reports the default count."""
    # First set a non-default value.
    _run(["perf-budgets", "set", "ui.tab_overview", "--max-p95", "0.5"], capsys)
    # Then reset.
    code, out, _ = _run(["perf-budgets", "reset", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["reset"] is True
    assert payload["default_count"] > 0


def test_perf_budgets_check_returns_count_dict(capsys) -> None:
    """`check` runs check_and_alert and prints the count dict shape."""
    code, out, _ = _run(["perf-budgets", "check", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert set(payload.keys()) == {"checked", "breached", "alerted", "skipped_cooldown"}


# ─── anomalies ───────────────────────────────────────────────────────────

def test_anomalies_configs_returns_defaults(capsys) -> None:
    """`anomalies configs --json` returns the default config set."""
    code, out, _ = _run(["anomalies", "configs", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert len(payload) > 0
    for row in payload:
        assert set(row.keys()) >= {
            "metric_id", "enabled", "method", "lookback_days",
            "z_threshold", "min_samples",
        }


def test_anomalies_configs_text_table(capsys) -> None:
    """Plain output renders the ASCII table with headers."""
    code, out, _ = _run(["anomalies", "configs"], capsys)
    assert code == 0
    assert "metric_id" in out
    assert "method" in out


def test_anomalies_check_returns_count_dict(capsys) -> None:
    """`anomalies check --json` returns a payload with counts + results."""
    code, out, _ = _run(["anomalies", "check", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert set(payload.keys()) >= {"counts", "results"}
    assert set(payload["counts"].keys()) == {
        "checked", "detected", "alerted", "skipped_cooldown",
    }


def test_anomalies_enable_then_disable(capsys) -> None:
    """`enable` flips on; `disable` flips off."""
    code, out, _ = _run(["anomalies", "enable", "bdi", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["metric_id"] == "bdi"
    assert payload["saved"] is True
    assert payload["action"] == "enable"

    code, out, _ = _run(["anomalies", "disable", "bdi", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["saved"] is True
    assert payload["action"] == "disable"

    # Verify the disable persisted.
    from engine.anomaly_detect import get_anomaly_configs
    cfgs = {c.metric_id: c for c in get_anomaly_configs()}
    assert cfgs["bdi"].enabled is False


def test_anomalies_set_updates_threshold(capsys) -> None:
    """`set --z-threshold N --lookback-days N` persists both."""
    code, out, _ = _run(
        ["anomalies", "set", "bdi",
         "--z-threshold", "4.0", "--lookback-days", "45", "--json"],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["saved"] is True
    assert payload["z_threshold"] == 4.0
    assert payload["lookback_days"] == 45

    from engine.anomaly_detect import get_anomaly_configs
    cfgs = {c.metric_id: c for c in get_anomaly_configs()}
    assert cfgs["bdi"].z_threshold == 4.0
    assert cfgs["bdi"].lookback_days == 45


def test_anomalies_set_unknown_metric_reports_error(capsys) -> None:
    """`set` on a metric_id that has no config reports unknown."""
    code, out, _ = _run(
        ["anomalies", "set", "no_such_metric",
         "--z-threshold", "3.0", "--json"],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["saved"] is False
    assert "unknown" in payload["error"].lower()


# ─── users ───────────────────────────────────────────────────────────────

def test_users_list_empty(capsys) -> None:
    code, out, _ = _run(["users", "list"], capsys)
    assert code == 0
    # Empty user table → "(no rows)" line; just check the handler ran.
    assert out.strip() != ""


def test_users_create_happy_path(capsys) -> None:
    """A valid username + 8+ char password creates a user; the new
    user's metadata is echoed back without the password."""
    code, out, _ = _run(
        ["users", "create", "alice", "--password", "longenough"],
        capsys,
    )
    assert code == 0
    assert "alice" in out
    # The password MUST NOT appear in the output stream.
    assert "longenough" not in out


def test_users_create_missing_password_is_usage_error(capsys) -> None:
    """Argparse rejects missing required --password with exit 2."""
    code, _, err = _run(["users", "create", "alice"], capsys)
    assert code == 2
    # Argparse routes the diagnostic to stderr.
    assert "password" in err.lower()


def test_users_create_duplicate_exits_1(capsys) -> None:
    """signup() returns None on duplicate; the CLI converts that to
    exit 1 with a stderr message — NOT a traceback."""
    code, _, _ = _run(
        ["users", "create", "bob", "--password", "longenough"],
        capsys,
    )
    assert code == 0
    code2, _, err2 = _run(
        ["users", "create", "bob", "--password", "longenough"],
        capsys,
    )
    assert code2 == 1
    assert "error:" in err2.lower() or "signup failed" in err2.lower()


def test_users_list_after_create(capsys) -> None:
    """After signup, the users-list table contains the username."""
    _run(["users", "create", "carol", "--password", "longenough"], capsys)
    code, out, _ = _run(["users", "list"], capsys)
    assert code == 0
    assert "carol" in out


# ─── tokens ──────────────────────────────────────────────────────────────

def test_tokens_list_empty(capsys) -> None:
    """No tokens for an unknown user → "(no rows)" table."""
    code, out, _ = _run(["tokens", "list", "--user-id", "u-unknown"], capsys)
    assert code == 0
    assert out.strip() != ""


def test_tokens_create_prints_raw_token_once(capsys) -> None:
    """The raw token MUST appear EXACTLY ONCE on stdout. This is a
    security property — if the CLI accidentally double-prints it, a
    naive consumer that splits on whitespace might paste both copies."""
    code, out, _ = _run(
        ["tokens", "create", "u-1", "--label", "ci-bot"],
        capsys,
    )
    assert code == 0
    # Find the "token: " line and extract the raw value.
    lines = [ln for ln in out.splitlines() if ln.startswith("token:")]
    assert len(lines) == 1
    raw = lines[0].split("token:", 1)[1].strip()
    assert len(raw) >= 32  # url-safe base64 of 32 bytes
    # The raw token MUST NOT appear anywhere else in the output.
    assert out.count(raw) == 1


def test_tokens_create_json_carries_token_field(capsys) -> None:
    """--json mode emits ``{"token": ..., "meta": {...}}``."""
    code, out, _ = _run(
        ["tokens", "create", "u-1", "--label", "ci-bot", "--json"],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert "token" in payload
    assert "meta" in payload
    # The raw token still appears exactly once in the JSON stream.
    raw = payload["token"]
    assert out.count(raw) == 1


def test_tokens_create_invalid_exits_1(capsys) -> None:
    """create_token returns None on empty label; CLI converts to exit 1."""
    code, _, err = _run(
        ["tokens", "create", "", "--label", "ci-bot"],
        capsys,
    )
    assert code == 1
    assert "error:" in err.lower()


def test_tokens_revoke_unknown_returns_false(capsys) -> None:
    """revoke_token returns False for an unknown token; CLI exits 0
    with revoked=False in the payload."""
    code, out, _ = _run(
        ["tokens", "revoke", "unknown-id", "--user-id", "u-1"],
        capsys,
    )
    assert code == 0
    assert "revoked" in out
    assert "False" in out


def test_tokens_create_then_list(capsys) -> None:
    """After creating a token, ``tokens list`` shows the label."""
    _run(["tokens", "create", "u-2", "--label", "personal"], capsys)
    code, out, _ = _run(["tokens", "list", "--user-id", "u-2"], capsys)
    assert code == 0
    assert "personal" in out


# ─── export ──────────────────────────────────────────────────────────────

def test_export_writes_to_custom_path(capsys, tmp_path) -> None:
    """build_export should write a tarball at the requested path; the
    CLI prints the output path on stdout."""
    out_path = tmp_path / "export.tar.gz"
    code, out, _ = _run(["export", "--output", str(out_path)], capsys)
    assert code == 0
    assert str(out_path) in out
    assert out_path.exists()


# ─── mfa ─────────────────────────────────────────────────────────────────

def _mk_user(username: str = "alice", password: str = "longenough") -> str:
    """Create a real user via auth.users.signup and return the user_id.

    MFA + settings handlers need an existing users row to operate on —
    they hit ``UPDATE users WHERE user_id = ?`` (mfa) or read the
    username for the provisioning URI (mfa enable). signup() is the
    public way to plant one in the same DB the CLI will read.
    """
    from auth.users import signup

    user = signup(username, password)
    assert user is not None
    return user.user_id


def test_mfa_enable_prints_secret_and_uri(capsys) -> None:
    """The text-mode output must carry BOTH the raw secret and the
    provisioning URI so an operator can either scan a QR or paste the
    secret into an authenticator app that doesn't ship a scanner."""
    uid = _mk_user("alice")
    code, out, _ = _run(["mfa", "enable", uid], capsys)
    assert code == 0
    assert "secret:" in out
    assert "provisioning_uri:" in out
    assert "otpauth://" in out  # canonical KeyURI form
    # Each piece appears EXACTLY ONCE — operator copy-paste safety.
    secret_lines = [ln for ln in out.splitlines() if ln.startswith("secret:")]
    assert len(secret_lines) == 1
    raw_secret = secret_lines[0].split("secret:", 1)[1].strip()
    assert out.count(raw_secret) == 2  # once on the secret: line, once inside the URI


def test_mfa_enable_persists_flag(capsys) -> None:
    """After ``mfa enable``, ``is_mfa_enabled`` should return True."""
    uid = _mk_user("alice")
    code, _, _ = _run(["mfa", "enable", uid], capsys)
    assert code == 0
    from auth.mfa import is_mfa_enabled
    assert is_mfa_enabled(uid) is True


def test_mfa_enable_unknown_user_exits_1(capsys) -> None:
    """enable_mfa returns False for an unknown user_id; the CLI must
    convert that to exit 1 with a stderr message (no traceback)."""
    code, _, err = _run(["mfa", "enable", "user-does-not-exist"], capsys)
    assert code == 1
    assert "error:" in err.lower()


def test_mfa_disable_round_trip(capsys) -> None:
    """enable then disable: the second call should leave is_mfa_enabled
    False and return exit 0."""
    uid = _mk_user("alice")
    _run(["mfa", "enable", uid], capsys)
    code, _, _ = _run(["mfa", "disable", uid], capsys)
    assert code == 0
    from auth.mfa import is_mfa_enabled
    assert is_mfa_enabled(uid) is False


def test_mfa_status_reflects_state(capsys) -> None:
    """status before enable → enabled=False; after enable → enabled=True."""
    uid = _mk_user("alice")
    code, out, _ = _run(["mfa", "status", uid, "--json"], capsys)
    assert code == 0
    assert json.loads(out)["enabled"] is False
    _run(["mfa", "enable", uid], capsys)
    code2, out2, _ = _run(["mfa", "status", uid, "--json"], capsys)
    assert code2 == 0
    assert json.loads(out2)["enabled"] is True


def test_mfa_enable_json_carries_all_fields(capsys) -> None:
    """--json output for enable carries secret, provisioning_uri,
    enabled, user_id."""
    uid = _mk_user("alice")
    code, out, _ = _run(["mfa", "enable", uid, "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["user_id"] == uid
    assert payload["enabled"] is True
    assert isinstance(payload["secret"], str) and len(payload["secret"]) >= 16
    assert payload["provisioning_uri"].startswith("otpauth://")


# ─── filters ─────────────────────────────────────────────────────────────

def _mk_preset(name: str, scope: str, user_id: str, payload: dict) -> None:
    """Plant a saved filter preset directly via the module API so the
    CLI's list/delete handlers have something to read."""
    from state.user_filters import FilterPreset, save_preset

    ok = save_preset(FilterPreset(name=name, scope=scope, payload=payload), user_id=user_id)
    assert ok is True


def test_filters_list_empty_returns_no_rows(capsys) -> None:
    """A user with no saved presets → "(no rows)" table, exit 0."""
    code, out, _ = _run(["filters", "list", "--user-id", "u-none"], capsys)
    assert code == 0
    assert out.strip() != ""


def test_filters_list_empty_json_returns_empty_list(capsys) -> None:
    code, out, _ = _run(["filters", "list", "--user-id", "u-none", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload == []


def test_filters_list_returns_saved_preset(capsys) -> None:
    _mk_preset("24h-critical", "alerts", "u-1", {"severity": "CRITICAL"})
    code, out, _ = _run(["filters", "list", "--user-id", "u-1"], capsys)
    assert code == 0
    assert "24h-critical" in out
    assert "alerts" in out


def test_filters_list_scope_filter(capsys) -> None:
    """--scope filters to that surface only."""
    _mk_preset("for-alerts", "alerts", "u-1", {})
    _mk_preset("for-reports", "reports", "u-1", {})
    code, out, _ = _run(
        ["filters", "list", "--user-id", "u-1", "--scope", "alerts", "--json"],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    names = [p["name"] for p in payload]
    assert names == ["for-alerts"]


def test_filters_delete_round_trip(capsys) -> None:
    """Plant a preset → delete it → list returns empty."""
    _mk_preset("to-delete", "alerts", "u-1", {"severity": "HIGH"})
    code, out, _ = _run(
        ["filters", "delete", "to-delete", "--scope", "alerts", "--user-id", "u-1", "--json"],
        capsys,
    )
    assert code == 0
    assert json.loads(out)["deleted"] is True
    # Confirm it's actually gone.
    from state.user_filters import load_presets
    assert load_presets(user_id="u-1") == []


def test_filters_delete_missing_returns_false(capsys) -> None:
    """Deleting a preset that never existed returns deleted=False, exit
    still 0 (the underlying call didn't raise)."""
    code, out, _ = _run(
        ["filters", "delete", "nope", "--scope", "alerts", "--user-id", "u-1", "--json"],
        capsys,
    )
    assert code == 0
    assert json.loads(out)["deleted"] is False


# ─── incidents ───────────────────────────────────────────────────────────

def test_incidents_list_empty(capsys) -> None:
    """No alerts → no incidents → "(no rows)" table."""
    code, out, _ = _run(["incidents", "list"], capsys)
    assert code == 0
    assert out.strip() != ""


def test_incidents_list_with_synthetic_alerts(capsys) -> None:
    """Insert two alerts that should correlate into one incident
    (same ticker, within the 30-minute window) — the incidents list
    must surface at least one row."""
    _mk_alert_row(severity="HIGH", idx=1)
    _mk_alert_row(severity="CRITICAL", idx=1)  # same idx → same TKR1 ticker
    code, out, _ = _run(["incidents", "list", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert len(payload) >= 1
    # Each incident carries the documented top-level fields.
    inc = payload[0]
    assert "incident_id" in inc
    assert "severity_max" in inc
    assert "alert_count" in inc


def test_incidents_stats_empty_returns_zeroed_shape(capsys) -> None:
    """Empty DB → zeroed dict with the documented keys still present."""
    code, out, _ = _run(["incidents", "stats", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["n_incidents"] == 0
    assert payload["n_total_alerts"] == 0
    assert payload["breakdown_by_dominant_type"] == {}


def test_incidents_stats_with_alerts(capsys) -> None:
    """With alerts present, the stats panel reports non-zero counts."""
    _mk_alert_row(severity="HIGH", idx=1)
    _mk_alert_row(severity="CRITICAL", idx=2)
    code, out, _ = _run(["incidents", "stats"], capsys)
    assert code == 0
    assert "n_incidents" in out
    assert "n_total_alerts" in out


# ─── settings ────────────────────────────────────────────────────────────

def test_settings_show_defaults_for_unknown_user(capsys) -> None:
    """A user that has never saved preferences → the defaults dataclass
    is returned (timezone=UTC, theme=auto, etc.)."""
    code, out, _ = _run(["settings", "show", "--user-id", "u-1", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["timezone"] == "UTC"
    assert payload["theme"] == "auto"
    assert payload["default_alert_severity"] == "LOW"


def test_settings_set_then_show_reflects_change(capsys) -> None:
    """settings set --timezone … should be visible to a follow-up show."""
    code, _, _ = _run(
        ["settings", "set", "--user-id", "u-1", "--timezone", "America/New_York"],
        capsys,
    )
    assert code == 0
    code2, out, _ = _run(["settings", "show", "--user-id", "u-1", "--json"], capsys)
    assert code2 == 0
    payload = json.loads(out)
    assert payload["timezone"] == "America/New_York"


def test_settings_set_multiple_keys_in_one_call(capsys) -> None:
    """Passing multiple flags at once applies all of them."""
    code, _, _ = _run(
        [
            "settings", "set", "--user-id", "u-1",
            "--theme", "dark",
            "--report-window", "60",
            "--alert-severity", "HIGH",
        ],
        capsys,
    )
    assert code == 0
    code2, out, _ = _run(["settings", "show", "--user-id", "u-1", "--json"], capsys)
    assert code2 == 0
    payload = json.loads(out)
    assert payload["theme"] == "dark"
    assert payload["default_report_window_days"] == 60
    assert payload["default_alert_severity"] == "HIGH"


def test_settings_set_no_flags_exits_1(capsys) -> None:
    """settings set with no preference flags is operator error → exit 1."""
    code, _, err = _run(["settings", "set", "--user-id", "u-1"], capsys)
    assert code == 1
    assert "error:" in err.lower()


# ─── argparse rejection paths ────────────────────────────────────────────

def test_unknown_subcommand_exits_2(capsys) -> None:
    """An unknown top-level subcommand → exit 2 (argparse rejected),
    NOT exit 1 (handler raised). The distinction matters for calling
    scripts that retry on transient failures."""
    code, _, err = _run(["bogus-command"], capsys)
    assert code == 2
    # Argparse routes its diagnostic to stderr; usage line is the cue.
    assert err.strip() != ""


def test_unknown_alerts_sub_exits_2(capsys) -> None:
    code, _, _ = _run(["alerts", "bogus"], capsys)
    assert code == 2


def test_no_command_exits_2(capsys) -> None:
    """No subcommand at all → exit 2."""
    code, _, _ = _run([], capsys)
    assert code == 2


# ─── --json validity check (one per command group) ───────────────────────

def test_every_json_subcommand_produces_valid_json(capsys) -> None:
    """A defence-in-depth test that every read-only --json handler
    emits JSON the stdlib can parse. The list mirrors the spec's CLI
    section."""
    # Reports list needs a list, telemetry needs a list, perf is a dict,
    # etc — parse and just assert no exception.
    invocations = [
        ["status", "--json"],
        ["alerts", "list", "--json"],
        ["alerts", "metrics", "--json"],
        ["channels", "list", "--json"],
        ["reports", "list", "--json"],
        ["reports", "stats", "--json"],
        ["telemetry", "usage", "--json"],
        ["telemetry", "recent", "--json"],
        ["perf", "summary", "--json"],
        ["health", "summary", "--json"],
        ["users", "list", "--json"],
        ["tokens", "list", "--user-id", "u-x", "--json"],
        # New read-only --json handlers from commit 3782580+
        ["mfa", "status", "u-x", "--json"],
        ["filters", "list", "--user-id", "u-x", "--json"],
        ["incidents", "list", "--json"],
        ["incidents", "stats", "--json"],
        ["settings", "show", "--user-id", "u-x", "--json"],
    ]
    for argv in invocations:
        code, out, err = _run(argv, capsys)
        assert code == 0, f"{argv} → exit {code}; stderr={err!r}"
        json.loads(out)  # raises if not valid JSON


# ─── schedules ───────────────────────────────────────────────────────────

def test_schedules_list_empty(capsys) -> None:
    """Empty schedules list prints '(no rows)' not a crash."""
    code, out, _ = _run(["schedules", "list", "--user-id", "alice"], capsys)
    assert code == 0
    assert out.strip() != ""


def test_schedules_create_and_list(capsys) -> None:
    """Creating a schedule shows up in the subsequent list call."""
    code, _, _ = _run(
        ["schedules", "create", "--user-id", "alice",
         "--name", "Morning Macro", "--cron", "0 9 * * *", "--json"],
        capsys,
    )
    assert code == 0

    code2, out2, _ = _run(
        ["schedules", "list", "--user-id", "alice", "--json"], capsys,
    )
    assert code2 == 0
    payload = json.loads(out2)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["name"] == "Morning Macro"
    assert payload[0]["cron_expr"] == "0 9 * * *"


def test_schedules_create_rejects_invalid_cron(capsys) -> None:
    """The CLI must exit 1 (not 0, not 2) on a bad cron — the handler
    raises ``RuntimeError`` from a bad ``validate_cron_expr`` and the
    top-level main converts to exit 1."""
    code, _, err = _run(
        ["schedules", "create", "--user-id", "alice",
         "--name", "broken", "--cron", "not a cron"],
        capsys,
    )
    assert code == 1
    assert "invalid cron" in err.lower()


def test_schedules_enable_disable_roundtrip(capsys) -> None:
    """Disable then enable flips the row's enabled flag both ways."""
    from engine.report_scheduler import load_schedules

    _run(
        ["schedules", "create", "--user-id", "alice",
         "--name", "x", "--cron", "0 9 * * *", "--json"],
        capsys,
    )
    schedules = load_schedules(user_id="alice")
    assert len(schedules) == 1
    sid = schedules[0].schedule_id

    code, _, _ = _run(["schedules", "disable", sid, "--user-id", "alice"], capsys)
    assert code == 0
    assert load_schedules(user_id="alice")[0].enabled is False

    code2, _, _ = _run(["schedules", "enable", sid, "--user-id", "alice"], capsys)
    assert code2 == 0
    assert load_schedules(user_id="alice")[0].enabled is True


def test_schedules_delete_removes_row(capsys) -> None:
    """``schedules delete`` removes the row; subsequent list is empty."""
    from engine.report_scheduler import load_schedules

    _run(
        ["schedules", "create", "--user-id", "alice",
         "--name", "x", "--cron", "0 9 * * *"],
        capsys,
    )
    sid = load_schedules(user_id="alice")[0].schedule_id

    code, _, _ = _run(
        ["schedules", "delete", sid, "--user-id", "alice", "--json"], capsys,
    )
    assert code == 0
    assert load_schedules(user_id="alice") == []


# ─── silences (v22) ──────────────────────────────────────────────────────

def test_silences_list_empty(capsys) -> None:
    """Empty silences list prints '(no rows)' not a crash."""
    from auth.users import signup
    user = signup("alice", "correct-password-123")
    assert user is not None
    code, out, _ = _run(
        ["silences", "list", "--user-id", user.user_id], capsys,
    )
    assert code == 0
    assert out.strip() != ""


def test_silences_create_and_list(capsys) -> None:
    """Creating a silence shows up in the subsequent list call."""
    from auth.users import signup
    user = signup("alice", "correct-password-123")
    assert user is not None

    code, _, _ = _run(
        [
            "silences", "create",
            "--user-id", user.user_id,
            "--duration-minutes", "60",
            "--rule-id", "rule_bdi",
            "--severity", "HIGH",
            "--reason", "FRED maintenance",
            "--json",
        ],
        capsys,
    )
    assert code == 0

    code2, out2, _ = _run(
        ["silences", "list", "--user-id", user.user_id, "--json"], capsys,
    )
    assert code2 == 0
    payload = json.loads(out2)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["rule_id"] == "rule_bdi"
    assert payload[0]["severity"] == "HIGH"
    assert payload[0]["reason"] == "FRED maintenance"


def test_silences_create_omits_optional_filters(capsys) -> None:
    """Omitted --rule-id / --ticker / --severity become NULL on the
    row → broadest possible silence (all alerts for the user)."""
    from auth.users import signup
    user = signup("alice", "correct-password-123")
    assert user is not None

    code, out, _ = _run(
        [
            "silences", "create",
            "--user-id", user.user_id,
            "--duration-minutes", "30",
            "--json",
        ],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["rule_id"] is None
    assert payload["ticker"] is None
    assert payload["severity"] is None


def test_silences_delete_per_user_scoping(capsys) -> None:
    """Alice cannot delete Bob's silence — the CLI surfaces this as
    ``deleted: False`` (exit 0; not a crash)."""
    from auth.users import signup
    from engine.alert_silences import create_silence, list_silences

    alice = signup("alice", "correct-password-123")
    bob = signup("bob", "correct-password-123")
    assert alice is not None and bob is not None

    s = create_silence(
        user_id=bob.user_id, duration_minutes=60,
        created_by_user_id=bob.user_id,
    )
    assert s is not None

    # Alice tries to delete bob's silence via the CLI.
    code, out, _ = _run(
        ["silences", "delete", s.silence_id,
         "--user-id", alice.user_id, "--json"],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["deleted"] is False
    # Bob's silence still exists.
    assert len(list_silences(user_id=bob.user_id)) == 1


def test_silences_list_include_expired(capsys) -> None:
    """``--include-expired`` surfaces silences whose expires_at has
    already passed (kept around for audit retention)."""
    from datetime import datetime, timedelta, timezone

    from auth.users import signup
    from engine.alert_silences import create_silence
    from state.db import get_connection

    user = signup("alice", "correct-password-123")
    assert user is not None
    s = create_silence(
        user_id=user.user_id, duration_minutes=60,
        created_by_user_id=user.user_id,
    )
    assert s is not None
    # Backdate so the silence is expired but still in the table.
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    very_past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE alert_silences SET starts_at = ?, expires_at = ? "
            "WHERE silence_id = ?",
            (very_past, past, s.silence_id),
        )

    # Without --include-expired → empty list.
    code, out, _ = _run(
        ["silences", "list", "--user-id", user.user_id, "--json"], capsys,
    )
    assert code == 0
    assert json.loads(out) == []

    # With --include-expired → the row surfaces.
    code2, out2, _ = _run(
        ["silences", "list", "--user-id", user.user_id,
         "--include-expired", "--json"],
        capsys,
    )
    assert code2 == 0
    payload = json.loads(out2)
    assert isinstance(payload, list) and len(payload) == 1


# ─── annotations (v23) ───────────────────────────────────────────────────

def test_annotations_list_empty(capsys) -> None:
    """Listing the thread for an alert with no annotations prints
    something — not a crash."""
    from auth.users import signup
    user = signup("alice", "correct-password-123")
    assert user is not None
    code, out, _ = _run(
        ["annotations", "list", "alert-bogus",
         "--user-id", user.user_id], capsys,
    )
    assert code == 0
    assert out.strip() != ""


def test_annotations_add_and_list(capsys) -> None:
    """Adding an annotation surfaces it in the subsequent list call,
    in created_at ASC order."""
    from auth.users import signup
    user = signup("alice", "correct-password-123")
    assert user is not None

    code, _, _ = _run(
        ["annotations", "add", "alert-1",
         "--user-id", user.user_id,
         "--body", "escalated to ops team",
         "--json"],
        capsys,
    )
    assert code == 0

    code2, out2, _ = _run(
        ["annotations", "list", "alert-1",
         "--user-id", user.user_id, "--json"],
        capsys,
    )
    assert code2 == 0
    payload = json.loads(out2)
    assert isinstance(payload, list) and len(payload) == 1
    assert payload[0]["body"] == "escalated to ops team"
    assert payload[0]["author_user_id"] == user.user_id


def test_annotations_add_empty_body_errors(capsys) -> None:
    """An empty body via the CLI yields exit 1 (handler raises) — the
    engine layer drops the write and the CLI surfaces that as a
    non-zero exit so calling scripts can retry / alarm."""
    from auth.users import signup
    user = signup("alice", "correct-password-123")
    assert user is not None
    # argparse rejects --body without a value (exit 2). To exercise
    # the engine-drop path, pass a whitespace-only string.
    code, _, err = _run(
        ["annotations", "add", "alert-1",
         "--user-id", user.user_id, "--body", "   "],
        capsys,
    )
    assert code == 1  # handler-raised
    assert "add_annotation failed" in err or err  # one-line stderr


def test_annotations_delete_per_author_scoping(capsys) -> None:
    """Bob cannot delete alice's annotation. The CLI surfaces this
    as ``deleted: False`` (exit 0; not a crash)."""
    from auth.users import signup
    from engine.alert_annotations import add_annotation, list_annotations

    alice = signup("alice", "correct-password-123")
    bob = signup("bob", "correct-password-123")
    assert alice is not None and bob is not None

    saved = add_annotation(
        "alert-1", "alice's note", user_id=alice.user_id,
    )
    assert saved is not None

    code, out, _ = _run(
        ["annotations", "delete", saved.annotation_id,
         "--user-id", bob.user_id, "--json"],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["deleted"] is False
    # alice's note still there.
    assert len(list_annotations("alert-1", user_id=alice.user_id)) == 1


def test_annotations_delete_by_author_succeeds(capsys) -> None:
    """The author can delete their own annotation via the CLI."""
    from auth.users import signup
    from engine.alert_annotations import add_annotation, list_annotations

    user = signup("alice", "correct-password-123")
    assert user is not None
    saved = add_annotation(
        "alert-1", "to delete", user_id=user.user_id,
    )
    assert saved is not None

    code, out, _ = _run(
        ["annotations", "delete", saved.annotation_id,
         "--user-id", user.user_id, "--json"],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["deleted"] is True
    assert list_annotations("alert-1", user_id=user.user_id) == []


def test_annotations_list_per_user_scoping(capsys) -> None:
    """Alice's listing on a shared alert_id does not surface bob's
    notes — per-user scoping is enforced on the read."""
    from auth.users import signup
    from engine.alert_annotations import add_annotation

    alice = signup("alice", "correct-password-123")
    bob = signup("bob", "correct-password-123")
    assert alice is not None and bob is not None
    add_annotation("alert-X", "alice note", user_id=alice.user_id)
    add_annotation("alert-X", "bob note", user_id=bob.user_id)

    code, out, _ = _run(
        ["annotations", "list", "alert-X",
         "--user-id", alice.user_id, "--json"],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert len(payload) == 1
    assert payload[0]["body"] == "alice note"


# ─── mfa recovery-codes / regenerate-codes (v21) ─────────────────────────

def test_mfa_recovery_codes_reports_unused_count(capsys) -> None:
    """``mfa recovery-codes <uid>`` reports the unused count and does
    NOT print the codes themselves (they're unrecoverable after the
    initial mint)."""
    uid = _mk_user("alice")
    # Before enable → 0 unused.
    code, out, _ = _run(["mfa", "recovery-codes", uid, "--json"], capsys)
    assert code == 0
    assert json.loads(out)["unused_count"] == 0

    # After enable → 10 unused (auto-mint).
    _run(["mfa", "enable", uid], capsys)
    code2, out2, _ = _run(["mfa", "recovery-codes", uid, "--json"], capsys)
    assert code2 == 0
    assert json.loads(out2)["unused_count"] == 10


def test_mfa_regenerate_codes_prints_fresh_batch(capsys) -> None:
    """``mfa regenerate-codes <uid>`` wipes the old batch and prints
    10 fresh plaintext codes. Each code must appear EXACTLY ONCE in
    stdout — operator copy-paste safety."""
    from auth.mfa import count_unused_recovery_codes

    uid = _mk_user("alice")
    _run(["mfa", "enable", uid], capsys)
    assert count_unused_recovery_codes(uid) == 10

    code, out, _ = _run(["mfa", "regenerate-codes", uid, "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["user_id"] == uid
    codes = payload["recovery_codes"]
    assert isinstance(codes, list) and len(codes) == 10
    # Each code appears exactly once in the json output (no dup leak).
    for c in codes:
        assert out.count(c) == 1


def test_mfa_regenerate_codes_unknown_user_exits_1(capsys) -> None:
    """An unknown user_id → regenerate returns [] → CLI converts to
    exit 1 with a stderr message (no traceback)."""
    code, _, err = _run(
        ["mfa", "regenerate-codes", "user-does-not-exist"], capsys
    )
    assert code == 1
    assert "error:" in err.lower()


# ─── invite create / list / revoke (v21) ─────────────────────────────────

def test_invite_create_prints_token_once(capsys) -> None:
    """The token MUST appear exactly once in stdout — same security
    contract as ``tokens create``."""
    admin_uid = _mk_user("alice")
    code, out, _ = _run(
        ["invite", "create", "--invited-by", admin_uid, "--role", "user"],
        capsys,
    )
    assert code == 0
    assert "invite_token:" in out
    # Extract the printed token and confirm exactly-once occurrence.
    token_lines = [
        ln for ln in out.splitlines() if ln.startswith("invite_token:")
    ]
    assert len(token_lines) == 1
    raw_token = token_lines[0].split("invite_token:", 1)[1].strip()
    assert out.count(raw_token) == 1


def test_invite_create_with_email_and_admin_role(capsys) -> None:
    """JSON output includes the email + role + expires_at fields. An
    admin-role invite must explicitly mint with role='admin'."""
    admin_uid = _mk_user("alice")
    code, out, _ = _run(
        [
            "invite", "create",
            "--invited-by", admin_uid,
            "--email", "bob",
            "--role", "admin",
            "--expires-days", "14",
            "--json",
        ],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["email"] == "bob"
    assert payload["role"] == "admin"
    assert payload["invited_by_user_id"] == admin_uid
    # Token is the right shape.
    assert isinstance(payload["invite_token"], str)
    assert len(payload["invite_token"]) == 32


def test_invite_list_shows_pending_then_empty_after_revoke(capsys) -> None:
    """List + revoke round-trip: a freshly-created invite shows in the
    listing, gets removed after revoke."""
    from auth.invitations import list_invitations

    admin_uid = _mk_user("alice")
    code, _, _ = _run(
        ["invite", "create", "--invited-by", admin_uid, "--json"], capsys
    )
    assert code == 0

    inv_list = list_invitations(invited_by_user_id=admin_uid)
    assert len(inv_list) == 1
    invite_id = inv_list[0].invite_id

    # List via CLI confirms one pending row.
    code2, out2, _ = _run(["invite", "list", "--json"], capsys)
    assert code2 == 0
    payload = json.loads(out2)
    assert isinstance(payload, list) and len(payload) == 1

    # Revoke → list is empty.
    code3, _, _ = _run(["invite", "revoke", invite_id, "--json"], capsys)
    assert code3 == 0
    code4, out4, _ = _run(["invite", "list", "--json"], capsys)
    assert code4 == 0
    assert json.loads(out4) == []


def test_invite_create_unknown_inviter_works(capsys) -> None:
    """The CLI does not validate that the inviter exists — the column
    is a free-form text field. This test pins the current contract so
    a future change is intentional."""
    code, out, _ = _run(
        ["invite", "create", "--invited-by", "u-anything", "--json"], capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["invited_by_user_id"] == "u-anything"


def test_invite_revoke_consumed_returns_false(capsys) -> None:
    """An invite that has already been consumed cannot be revoked —
    the row is part of the audit trail."""
    from auth.invitations import consume_invitation, create_invitation

    admin_uid = _mk_user("alice")
    invitee_uid = _mk_user("bob")
    inv = create_invitation(admin_uid)
    assert inv is not None
    assert consume_invitation(inv.invite_token, invitee_uid) is True

    code, out, _ = _run(
        ["invite", "revoke", inv.invite_id, "--json"], capsys
    )
    assert code == 0
    assert json.loads(out)["revoked"] is False


# ─── rules — config-as-code (feat/rules-as-code) ─────────────────────────

def _mk_rule(rule_id: str = "r1", name: str = "Test Rule") -> dict:
    """Build a minimal persisted-shape rule dict for the rules-CLI
    tests. Mirrors what the UI/template_to_alert_rule projection
    produces, so save_rules accepts it as-is and load_rules round-
    trips through the rules-YAML emitter."""
    return {
        "rule_id": rule_id,
        "id": rule_id,
        "name": name,
        "metric": "bdi",
        "threshold": 5.0,
        "condition": "Above",
        "severity": "HIGH",
        "email_notify": False,
        "enabled": True,
        "target_channels": [],
        "cooldown_minutes": 0,
        "flap_detection_enabled": False,
        "flap_window_minutes": 30,
        "flap_threshold_crossings": 5,
    }


def test_rules_export_to_stdout(capsys) -> None:
    """rules export writes the user's rules as YAML to stdout. The
    output must contain the schema header + a recognisable rule_id."""
    from engine.alert_engine_v2 import save_rules

    save_rules([_mk_rule("bdi-spike")])
    code, out, _ = _run(["rules", "export"], capsys)
    assert code == 0
    assert "schema_version: 1" in out
    assert "bdi-spike" in out


def test_rules_export_to_file_writes_path(capsys, tmp_path) -> None:
    """rules export --out FILE writes the YAML to disk and prints the
    path on stdout — the YAML itself is on disk so a calling shell can
    pipe / git-add it."""
    from engine.alert_engine_v2 import save_rules

    save_rules([_mk_rule("bdi-spike", "BDI spike")])
    out_file = tmp_path / "rules.yaml"
    code, out, _ = _run(["rules", "export", "--out", str(out_file)], capsys)
    assert code == 0
    assert str(out_file) in out
    assert out_file.exists()
    text = out_file.read_text()
    assert "bdi-spike" in text


def test_rules_import_persists_rules(capsys, tmp_path) -> None:
    """rules import --in FILE parses the YAML and saves via save_rules.
    A round-trip through load_rules must surface the rule_id we wrote."""
    from engine.alert_engine_v2 import load_rules
    from tools.rules_yaml import rules_to_yaml

    in_file = tmp_path / "import.yaml"
    in_file.write_text(rules_to_yaml([_mk_rule("imported-rule")]))
    code, out, _ = _run(["rules", "import", "--in", str(in_file)], capsys)
    assert code == 0
    assert "saved 1 rules" in out
    persisted = load_rules()
    rule_ids = [r.get("rule_id") or r.get("id") for r in persisted]
    assert "imported-rule" in rule_ids


def test_rules_import_dry_run_does_not_persist(capsys, tmp_path) -> None:
    """--dry-run must NOT call save_rules — the DB stays empty after a
    dry-run import. Operator should be able to preview safely."""
    from engine.alert_engine_v2 import load_rules
    from tools.rules_yaml import rules_to_yaml

    in_file = tmp_path / "preview.yaml"
    in_file.write_text(rules_to_yaml([_mk_rule("preview-rule")]))
    code, out, _ = _run(
        ["rules", "import", "--in", str(in_file), "--dry-run"], capsys
    )
    assert code == 0
    assert "dry-run" in out
    assert "preview-rule" in out
    # The DB must still be empty.
    assert load_rules() == []


def test_rules_diff_shows_added_and_removed(capsys, tmp_path) -> None:
    """diff vs the current set: an import file containing a NEW rule_id
    that's not in the DB produces an added line; a current rule_id that
    is NOT in the file produces a removed line."""
    from engine.alert_engine_v2 import save_rules
    from tools.rules_yaml import rules_to_yaml

    save_rules([_mk_rule("existing-rule", "Existing")])
    in_file = tmp_path / "new.yaml"
    in_file.write_text(rules_to_yaml([_mk_rule("brand-new-rule", "New")]))
    code, out, _ = _run(
        ["rules", "diff", "--in", str(in_file)], capsys
    )
    assert code == 0
    # Unified-diff format: removed lines prefixed '-', added '+'.
    assert "existing-rule" in out
    assert "brand-new-rule" in out


def test_rules_import_malformed_yaml_exits_1(capsys, tmp_path) -> None:
    """A YAML file with no salvageable content → exit 1 + stderr
    message. The CLI must not crash with a traceback — its contract
    promises a one-line stderr error and exit-1 on every handler
    failure mode."""
    in_file = tmp_path / "bad.yaml"
    in_file.write_text("    bad_indent_at_top_level: 1\n")
    code, _, err = _run(["rules", "import", "--in", str(in_file)], capsys)
    assert code == 1
    assert "error:" in err.lower()


# ─── rules export-csv / import-csv / diff-csv ────────────────────────────
#
# Mirror of the YAML subcommand tests above but for the CSV wire
# format. The defining contract is the same: export writes a
# deterministic CSV, import parses + replaces the user's rule set,
# diff shows a unified diff vs the live state.

def test_rules_export_csv_to_stdout(capsys) -> None:
    """rules export-csv writes the user's rules as CSV to stdout.
    Output must carry the header row (so the parser can map columns
    by name) and the rule_id we saved."""
    from engine.alert_engine_v2 import save_rules

    save_rules([_mk_rule("bdi-spike")])
    code, out, _ = _run(["rules", "export-csv"], capsys)
    assert code == 0
    # Header row is present (rule_id is the first column).
    assert "rule_id" in out
    # Rule body is present.
    assert "bdi-spike" in out


def test_rules_export_csv_to_file_writes_path(capsys, tmp_path) -> None:
    """rules export-csv --out FILE writes the CSV to disk and prints
    the path on stdout. The file on disk carries the BOM so Excel
    opens it without mojibake."""
    from engine.alert_engine_v2 import save_rules

    save_rules([_mk_rule("bdi-spike", "BDI spike")])
    out_file = tmp_path / "rules.csv"
    code, out, _ = _run(
        ["rules", "export-csv", "--out", str(out_file)], capsys
    )
    assert code == 0
    assert str(out_file) in out
    assert out_file.exists()
    # The file starts with the UTF-8 BOM (\xef\xbb\xbf).
    raw = out_file.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = out_file.read_text(encoding="utf-8")
    assert "bdi-spike" in text


def test_rules_import_csv_persists_rules(capsys, tmp_path) -> None:
    """rules import-csv --in FILE parses the CSV and saves via
    save_rules. A round-trip through load_rules must surface the
    rule_id we wrote."""
    from engine.alert_engine_v2 import load_rules
    from tools.rules_csv import rules_to_csv

    in_file = tmp_path / "import.csv"
    in_file.write_text(
        rules_to_csv([_mk_rule("imported-rule")]),
        encoding="utf-8",
    )
    code, out, _ = _run(["rules", "import-csv", "--in", str(in_file)], capsys)
    assert code == 0
    assert "saved 1 rules" in out
    persisted = load_rules()
    rule_ids = [r.get("rule_id") or r.get("id") for r in persisted]
    assert "imported-rule" in rule_ids


def test_rules_import_csv_dry_run_does_not_persist(capsys, tmp_path) -> None:
    """--dry-run must NOT call save_rules — the DB stays empty
    after a dry-run import. Operator can preview safely."""
    from engine.alert_engine_v2 import load_rules
    from tools.rules_csv import rules_to_csv

    in_file = tmp_path / "preview.csv"
    in_file.write_text(
        rules_to_csv([_mk_rule("preview-rule")]),
        encoding="utf-8",
    )
    code, out, _ = _run(
        ["rules", "import-csv", "--in", str(in_file), "--dry-run"], capsys,
    )
    assert code == 0
    assert "dry-run" in out
    assert "preview-rule" in out
    assert load_rules() == []


def test_rules_diff_csv_shows_added_and_removed(capsys, tmp_path) -> None:
    """diff-csv vs the current set: a CSV file containing a NEW
    rule_id that's not in the DB produces an added line; a current
    rule_id NOT in the file produces a removed line."""
    from engine.alert_engine_v2 import save_rules
    from tools.rules_csv import rules_to_csv

    save_rules([_mk_rule("existing-rule", "Existing")])
    in_file = tmp_path / "new.csv"
    in_file.write_text(
        rules_to_csv([_mk_rule("brand-new-rule", "New")]),
        encoding="utf-8",
    )
    code, out, _ = _run(
        ["rules", "diff-csv", "--in", str(in_file)], capsys,
    )
    assert code == 0
    # Unified-diff: both rule_ids appear in the diff body.
    assert "existing-rule" in out
    assert "brand-new-rule" in out


def test_rules_import_csv_malformed_exits_1(capsys, tmp_path) -> None:
    """A CSV file with no salvageable content → exit 1 + stderr.
    Same contract as the YAML variant: one-line stderr error, no
    traceback."""
    in_file = tmp_path / "bad.csv"
    # Header has rule_id but no valid severity in the only data row —
    # the only row is rejected and the parser returns ([], [warning]).
    in_file.write_text(
        "rule_id,name,metric,threshold_pct,severity\n"
        "r1,bad,bdi,5.0,BOGUS\n",
        encoding="utf-8",
    )
    code, _, err = _run(["rules", "import-csv", "--in", str(in_file)], capsys)
    assert code == 1
    assert "error:" in err.lower()


# ─── audit export ─────────────────────────────────────────────────────────
#
# The CLI subcommand bridges ``utils.audit_export`` → operator shell.
# Defining properties under test:
#   * Default output is JSONL on stdout; row count + elapsed go on
#     stderr so a calling pipe can capture the JSONL cleanly.
#   * --out FILE writes the JSONL to disk in the streaming path,
#     prints the path on stdout, count on stderr.
#   * --user-id / --action / --since / --until filters propagate
#     through to query_audit (the audit-export module already has
#     its own unit tests for the filter semantics; here we just
#     confirm the CLI doesn't drop the flag on the floor).
#   * Empty result → empty stdout + exit 0 (NOT 1 — an empty
#     export is a legitimate state for a SIEM pull).
#   * Bad ISO-8601 in --since/--until → exit 1 with stderr message
#     (not a stacktrace).

def _seed_audit_row(action: str, user_id: str = "u-1", detail: dict | None = None) -> None:
    """Plant one audit row via the real record_audit path so the
    CLI's read side has something to export."""
    from auth.audit import record_audit
    record_audit(action, user_id=user_id, detail=detail or {})


def test_audit_export_to_stdout_emits_jsonl(capsys) -> None:
    """Default invocation (no --out) writes JSONL to stdout and the
    progress line to stderr. Each line is independently json-loads-able."""
    _seed_audit_row("login_success", "u-1")
    _seed_audit_row("save_rules", "u-1")
    code, out, err = _run(["audit", "export", "--user-id", "u-1"], capsys)
    assert code == 0
    # Stdout carries JSONL — split, parse each line, confirm count.
    lines = [ln for ln in out.split("\n") if ln.strip()]
    assert len(lines) == 2
    parsed = [json.loads(ln) for ln in lines]
    assert {p["action"] for p in parsed} == {"login_success", "save_rules"}
    # Progress on STDERR.
    assert "exported 2 rows" in err


def test_audit_export_to_file_writes_path(capsys, tmp_path) -> None:
    """--out PATH writes the JSONL to disk, prints the path on stdout,
    and the row count + path on stderr. The file content is valid
    JSONL with the expected row count."""
    for i in range(5):
        _seed_audit_row("looped", "u-1", detail={"i": i})
    out_path = tmp_path / "audit.jsonl"
    code, out, err = _run(
        ["audit", "export", "--user-id", "u-1", "--out", str(out_path)],
        capsys,
    )
    assert code == 0
    assert str(out_path) in out
    assert "exported 5 rows" in err
    assert out_path.exists()
    body = out_path.read_text()
    lines = [ln for ln in body.split("\n") if ln.strip()]
    assert len(lines) == 5
    # Each line parses; the action verb survives the round-trip.
    parsed = [json.loads(ln) for ln in lines]
    assert all(p["action"] == "looped" for p in parsed)


def test_audit_export_user_id_filter(capsys) -> None:
    """--user-id u-A must drop u-B's rows. Per-user scoping is the
    core safety property of the export — a multi-tenant Splunk run
    that mis-routes rows is a privacy incident."""
    _seed_audit_row("login_success", "u-A")
    _seed_audit_row("login_success", "u-B")
    code, out, _ = _run(["audit", "export", "--user-id", "u-A"], capsys)
    assert code == 0
    lines = [ln for ln in out.split("\n") if ln.strip()]
    parsed = [json.loads(ln) for ln in lines]
    assert all(p["user_id"] == "u-A" for p in parsed)


def test_audit_export_action_filter(capsys) -> None:
    """--action filters to one verb only."""
    _seed_audit_row("login_success", "u-1")
    _seed_audit_row("save_rules", "u-1")
    _seed_audit_row("login_success", "u-1")
    code, out, _ = _run(
        ["audit", "export", "--user-id", "u-1", "--action", "save_rules"],
        capsys,
    )
    assert code == 0
    lines = [ln for ln in out.split("\n") if ln.strip()]
    parsed = [json.loads(ln) for ln in lines]
    assert len(parsed) == 1
    assert parsed[0]["action"] == "save_rules"


def test_audit_export_since_until_window(capsys) -> None:
    """--since / --until bracket the export window. We use a since=now-1min
    pair to bracket present-day rows in (--since included) / out
    (--until excluded)."""
    _seed_audit_row("ut_cli_window", "u-1")
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    code1, out1, _ = _run(
        ["audit", "export", "--user-id", "u-1", "--since", past],
        capsys,
    )
    assert code1 == 0
    assert "ut_cli_window" in out1
    code2, out2, _ = _run(
        ["audit", "export", "--user-id", "u-1", "--until", past],
        capsys,
    )
    assert code2 == 0
    assert "ut_cli_window" not in out2


def test_audit_export_no_matching_rows_exits_0_with_empty_stdout(capsys) -> None:
    """An empty result must still exit 0 (a SIEM pull that finds
    nothing is a legitimate state) and stdout must be empty (NOT
    a blank line). Stderr still reports the 0-row count so the
    operator sees the pull happened."""
    code, out, err = _run(
        ["audit", "export", "--user-id", "user-with-no-rows"],
        capsys,
    )
    assert code == 0
    # Stdout EXACTLY empty — no trailing newline, no whitespace.
    assert out == ""
    assert "exported 0 rows" in err


def test_audit_export_bad_iso_since_exits_1(capsys) -> None:
    """A malformed ISO-8601 --since must surface as exit 1 with a
    helpful stderr message — NOT as a traceback from datetime.
    The CLI contract (every handler failure → single-line stderr +
    exit 1) is the same one ``mfa enable`` already pins; this just
    extends it to the audit handler."""
    code, _, err = _run(
        ["audit", "export", "--since", "not-an-iso-date"],
        capsys,
    )
    assert code == 1
    assert "error:" in err.lower()


# ─── escalations (v24) ───────────────────────────────────────────────────
#
# The escalations CLI surface mirrors the API + UI: list / add / delete
# (one step) / clear (whole chain). Every command is per-user scoped via
# --user-id; add additionally validates that the supplied --channel-id
# exists in the user's channel set (better UX than letting the engine
# fail silently at dispatch time).


def _make_channel(user_id: str, channel_id: str = "ch_test") -> str:
    """Persist one delivery channel for ``user_id`` and return its id."""
    from engine.alert_delivery import DeliveryChannel, save_channel

    ch = DeliveryChannel(
        channel_id=channel_id,
        name=f"Test {channel_id}",
        kind="slack",
        target=f"https://hooks.example.com/{channel_id}",
        severity_threshold="LOW",
        enabled=True,
    )
    save_channel(ch, user_id=user_id)
    return ch.channel_id


def test_escalations_list_empty(capsys) -> None:
    """An empty chain prints '(no rows)' not a crash."""
    from auth.users import signup
    user = signup("alice", "correct-password-123")
    assert user is not None

    code, out, _ = _run(
        ["escalations", "list", "rule_x",
         "--user-id", user.user_id],
        capsys,
    )
    assert code == 0
    assert out.strip() != ""


def test_escalations_add_and_list(capsys) -> None:
    """Adding a step surfaces it in the subsequent list call."""
    from auth.users import signup
    user = signup("alice", "correct-password-123")
    assert user is not None
    cid = _make_channel(user.user_id, "ch_one")

    code, _, _ = _run(
        ["escalations", "add", "rule_x",
         "--user-id", user.user_id,
         "--step", "1",
         "--after-minutes", "15",
         "--channel-id", cid,
         "--json"],
        capsys,
    )
    assert code == 0

    code2, out2, _ = _run(
        ["escalations", "list", "rule_x",
         "--user-id", user.user_id, "--json"],
        capsys,
    )
    assert code2 == 0
    payload = json.loads(out2)
    assert isinstance(payload, list) and len(payload) == 1
    assert payload[0]["step_number"] == 1
    assert payload[0]["after_minutes"] == 15
    assert payload[0]["channel_id"] == cid


def test_escalations_delete_per_user_scoping(capsys) -> None:
    """Bob cannot delete alice's chain step by knowing the chain_id.
    The CLI surfaces this as ``deleted: False`` (exit 0; not a crash)."""
    from auth.users import signup
    from engine.alert_escalation import add_escalation_step, get_escalation_chain

    alice = signup("alice", "correct-password-123")
    bob = signup("bob", "correct-password-123")
    assert alice is not None and bob is not None
    cid = _make_channel(alice.user_id, "ch_a")

    step = add_escalation_step(
        rule_id="rule_x", user_id=alice.user_id,
        step_number=1, after_minutes=15, channel_id=cid,
    )
    assert step is not None

    # Bob tries to delete alice's step via the CLI.
    code, out, _ = _run(
        ["escalations", "delete", step.chain_id,
         "--user-id", bob.user_id, "--json"],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["deleted"] is False
    # Alice's step still exists.
    assert len(get_escalation_chain("rule_x", user_id=alice.user_id)) == 1


def test_escalations_clear_bulk_removes_all_steps(capsys) -> None:
    """``clear`` bulk-deletes every step in a rule's chain."""
    from auth.users import signup
    from engine.alert_escalation import (
        add_escalation_step, get_escalation_chain,
    )

    user = signup("alice", "correct-password-123")
    assert user is not None
    cid = _make_channel(user.user_id, "ch_one")

    for n in (1, 2, 3):
        s = add_escalation_step(
            rule_id="rule_x", user_id=user.user_id,
            step_number=n, after_minutes=n * 15, channel_id=cid,
        )
        assert s is not None
    assert len(get_escalation_chain("rule_x", user_id=user.user_id)) == 3

    code, out, _ = _run(
        ["escalations", "clear", "rule_x",
         "--user-id", user.user_id, "--json"],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["deleted_steps"] == 3
    assert get_escalation_chain("rule_x", user_id=user.user_id) == []


def test_escalations_add_unknown_rule_id_is_fine(capsys) -> None:
    """``add`` does not validate the rule_id (rules can be created
    after the chain). Only the channel_id is validated up front."""
    from auth.users import signup
    user = signup("alice", "correct-password-123")
    assert user is not None
    cid = _make_channel(user.user_id, "ch_one")

    # A rule_id that does not (yet) exist still persists — the chain
    # row is the system of record, not a foreign key into rules.
    code, _, _ = _run(
        ["escalations", "add", "rule_not_yet_created",
         "--user-id", user.user_id,
         "--step", "1",
         "--after-minutes", "15",
         "--channel-id", cid,
         "--json"],
        capsys,
    )
    assert code == 0


def test_escalations_add_unknown_channel_id_rejected(capsys) -> None:
    """A channel_id that does not exist in the user's set yields
    exit 1 with a clear error message — the CLI validates BEFORE
    the engine write so the operator gets immediate feedback."""
    from auth.users import signup
    user = signup("alice", "correct-password-123")
    assert user is not None
    # Note: we deliberately do NOT create the channel — the CLI
    # should reject the add up front.

    code, _, err = _run(
        ["escalations", "add", "rule_x",
         "--user-id", user.user_id,
         "--step", "1",
         "--after-minutes", "15",
         "--channel-id", "ch_does_not_exist"],
        capsys,
    )
    assert code == 1
    assert "channel_id" in err and "ch_does_not_exist" in err


def test_escalations_add_other_users_channel_rejected(capsys) -> None:
    """Alice cannot add a step targeting bob's channel — the CLI's
    per-user validation rejects cross-tenant channel references at
    write time. Otherwise the chain row would persist but escalate_alert
    would silently fail at dispatch time."""
    from auth.users import signup
    user_a = signup("alice", "correct-password-123")
    user_b = signup("bob", "correct-password-123")
    assert user_a is not None and user_b is not None
    # Bob owns this channel; alice does not.
    bob_cid = _make_channel(user_b.user_id, "ch_bob")

    code, _, err = _run(
        ["escalations", "add", "rule_x",
         "--user-id", user_a.user_id,
         "--step", "1",
         "--after-minutes", "15",
         "--channel-id", bob_cid],
        capsys,
    )
    assert code == 1
    assert "channel_id" in err
    assert bob_cid in err


# ─── digest ────────────────────────────────────────────────────────────────

def test_digest_config_defaults_disabled(capsys) -> None:
    """A brand-new user has no row → config reports disabled defaults."""
    code, out, _ = _run(
        ["digest", "config", "--user-id", "u-new", "--json"], capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["enabled"] is False
    assert payload["day_of_week"] == "monday"
    assert payload["hour_utc"] == 14
    assert payload["channel_ids"] == []


def test_digest_enable_round_trip(capsys) -> None:
    """``digest enable`` persists, ``digest config`` reads it back."""
    code, out, _ = _run(
        ["digest", "enable",
         "--user-id", "u1",
         "--channels", "c1,c2",
         "--day-of-week", "tuesday",
         "--hour", "9",
         "--json"],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["enabled"] is True
    assert payload["day_of_week"] == "tuesday"
    assert payload["hour_utc"] == 9
    assert payload["channel_ids"] == ["c1", "c2"]

    code2, out2, _ = _run(
        ["digest", "config", "--user-id", "u1", "--json"], capsys,
    )
    assert code2 == 0
    cfg = json.loads(out2)
    assert cfg["enabled"] is True
    assert cfg["day_of_week"] == "tuesday"
    assert cfg["hour_utc"] == 9
    assert cfg["channel_ids"] == ["c1", "c2"]


def test_digest_disable_clears_config(capsys) -> None:
    """``digest disable`` clears a previously-enabled config."""
    # Enable first
    _run(
        ["digest", "enable", "--user-id", "u1",
         "--channels", "c1", "--json"],
        capsys,
    )
    # Now disable
    code, out, _ = _run(
        ["digest", "disable", "--user-id", "u1", "--json"], capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["disabled"] is True

    # Subsequent config call returns disabled defaults
    code2, out2, _ = _run(
        ["digest", "config", "--user-id", "u1", "--json"], capsys,
    )
    assert code2 == 0
    cfg = json.loads(out2)
    assert cfg["enabled"] is False
    assert cfg["channel_ids"] == []


def test_digest_preview_prints_markdown(capsys) -> None:
    """``digest preview`` prints a non-empty markdown body."""
    code, out, _ = _run(
        ["digest", "preview", "--user-id", "u1"], capsys,
    )
    assert code == 0
    # The renderer always emits a header line
    assert "Weekly Digest" in out
    assert "Headline" in out


def test_digest_send_now_no_channels_succeeds_with_zero_results(capsys) -> None:
    """Send-now with no enabled channels exits cleanly + reports zero dispatches."""
    code, out, _ = _run(
        ["digest", "send-now", "--user-id", "u1", "--json"], capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["dispatched"] == 0
    assert payload["successes"] == 0
    assert payload["results"] == []
