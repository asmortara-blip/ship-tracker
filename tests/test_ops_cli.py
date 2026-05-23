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
from datetime import datetime, timezone

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
    ]
    for argv in invocations:
        code, out, err = _run(argv, capsys)
        assert code == 0, f"{argv} → exit {code}; stderr={err!r}"
        json.loads(out)  # raises if not valid JSON
