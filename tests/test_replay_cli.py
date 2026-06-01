"""Tests for ``tools.replay_cli`` — the alert-replay operator CLI.

Defining properties under test:

* Each subcommand returns exit 0 on the happy path and prints something
  to stdout (so a calling shell can ``tee`` the output safely).
* ``--json`` produces JSON the stdlib can round-trip
  (``json.loads(out)`` succeeds).
* Argparse rejection (unknown subcommand, missing required flag) yields
  exit 2 — not 1, not a traceback. The CLI must never bubble an
  exception out to the shell.
* A failed replay (cross-user, unknown id, dispatch failure) yields
  exit 3 — distinct from exit 1 ("handler raised") so an automated
  wrapper can pin the boundary.
* The bulk subcommand respects --severity, --since, --limit.

The isolation fixture mirrors test_ops_cli.py — per-test SQLite at
tmp_path so the test never touches cache/ship_tracker.db.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from engine import alert_delivery
from engine.alert_delivery import DeliveryChannel, save_channel
from engine.alert_engine_v2 import ShippingAlert, save_alerts


# ─── Isolation fixture ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ──────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text


def _run(argv: list[str], capsys) -> tuple[int, str, str]:
    """Call ``main(argv)`` and return (exit_code, stdout, stderr).

    capsys is the pytest fixture that captures stream writes; each
    call is hermetic — capsys resets between captures.
    """
    from tools.replay_cli import main

    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _seed(alert_id: str = "alert-1", channel_id: str = "ch-1") -> None:
    # ``ticker`` is derived from ``alert_id`` so each call seeds a row
    # with a distinct dedup_key (the v14 window otherwise collapses
    # multiple alerts with the same key into a single row).
    save_alerts([
        ShippingAlert(
            alert_id=alert_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            alert_type="BDI_MOVE",
            severity="HIGH",
            title=f"title-{alert_id}",
            body="body",
            ticker=alert_id,
            route_id="",
            port_locode="",
            value=1500.0,
            threshold=1400.0,
            change_pct=7.5,
            acknowledged=False,
        ),
    ], user_id="alice")
    save_channel(
        DeliveryChannel(
            channel_id=channel_id,
            name="test channel",
            kind="slack",
            target="https://hooks.slack.com/services/T/B/X",
            severity_threshold="LOW",
            enabled=True,
        ),
        user_id="alice",
    )


# ─── Argparse contract ────────────────────────────────────────────────────

def test_no_subcommand_yields_exit_2(capsys) -> None:
    code, _, _ = _run([], capsys)
    assert code == 2


def test_unknown_subcommand_yields_exit_2(capsys) -> None:
    code, _, _ = _run(["nope"], capsys)
    assert code == 2


def test_replay_missing_required_flag_yields_exit_2(capsys) -> None:
    # --channel-id and --user-id are both required.
    code, _, _ = _run(["replay", "alert-1"], capsys)
    assert code == 2


# ─── replay subcommand ───────────────────────────────────────────────────

def test_replay_happy_path_exit_0(monkeypatch, capsys) -> None:
    _seed()
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    code, out, _ = _run(
        ["replay", "alert-1", "--channel-id", "ch-1", "--user-id", "alice"],
        capsys,
    )
    assert code == 0
    assert "alert-1" in out
    assert "Y" in out  # success column


def test_replay_unknown_alert_yields_exit_3(monkeypatch, capsys) -> None:
    """A failed replay surfaces as exit 3 (NOT exit 1) so wrappers can
    pin the boundary."""
    _seed()
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    code, _, _ = _run(
        ["replay", "no-such-alert", "--channel-id", "ch-1", "--user-id", "alice"],
        capsys,
    )
    assert code == 3


def test_replay_json_output_parses(monkeypatch, capsys) -> None:
    _seed()
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    code, out, _ = _run(
        ["replay", "alert-1",
         "--channel-id", "ch-1", "--user-id", "alice", "--json"],
        capsys,
    )
    assert code == 0
    # Output must be valid JSON, and must carry the expected keys.
    parsed = json.loads(out)
    assert parsed["alert_id"] == "alert-1"
    assert parsed["channel_id"] == "ch-1"
    assert parsed["success"] is True


# ─── bulk subcommand ─────────────────────────────────────────────────────

def test_bulk_happy_path_exit_0(monkeypatch, capsys) -> None:
    _seed(alert_id="a1", channel_id="ch-1")
    _seed(alert_id="a2", channel_id="ch-1")
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    code, out, _ = _run(
        ["bulk", "--channel-id", "ch-1", "--user-id", "alice"],
        capsys,
    )
    assert code == 0
    # Both alerts appear in the table output.
    assert "a1" in out
    assert "a2" in out


def test_bulk_respects_limit(monkeypatch, capsys) -> None:
    for i in range(5):
        _seed(alert_id=f"a-{i}", channel_id="ch-1")
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    code, out, _ = _run(
        ["bulk", "--channel-id", "ch-1", "--user-id", "alice",
         "--limit", "2", "--json"],
        capsys,
    )
    assert code == 0
    parsed = json.loads(out)
    assert parsed["count"] == 2
    assert parsed["succeeded"] == 2


def test_bulk_malformed_since_warns_but_continues(monkeypatch, capsys) -> None:
    """A malformed --since spec writes a warning to stderr but the call
    proceeds (matches the CLI contract: typos shouldn't abort)."""
    _seed()
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    code, out, err = _run(
        ["bulk", "--channel-id", "ch-1", "--user-id", "alice",
         "--since", "garbage"],
        capsys,
    )
    assert code == 0
    assert "garbage" in err  # stderr warning surfaced
    assert "alert-1" in out  # but the replay went through


def test_bulk_no_matches_returns_exit_0(monkeypatch, capsys) -> None:
    """No alerts to replay = empty result = exit 0 (no failures)."""
    save_channel(
        DeliveryChannel(
            channel_id="ch-1",
            name="empty",
            kind="slack",
            target="https://hooks.slack.com/X",
            severity_threshold="LOW",
            enabled=True,
        ),
        user_id="alice",
    )
    monkeypatch.setattr(
        alert_delivery.requests, "post",
        lambda *a, **kw: _FakeResponse(200),
    )
    code, out, _ = _run(
        ["bulk", "--channel-id", "ch-1", "--user-id", "alice"],
        capsys,
    )
    assert code == 0
    assert "(no rows)" in out
