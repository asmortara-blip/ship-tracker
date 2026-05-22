"""Tests for the per-channel quiet-hours feature (schema v13).

A ``DeliveryChannel`` can carry a daily HH:MM window (``quiet_start`` /
``quiet_end``) during which ``deliver_alert`` suppresses outbound
deliveries — useful for "don't page me at 3am unless it's CRITICAL".
``quiet_override_critical`` (default True) lets CRITICAL alerts page
through anyway.

Covers:
  - DeliveryChannel defaults (empty quiet window + override=True)
  - save_channel + load_channels round-trip the three new fields
    across slack / email kinds + alongside digest_mode
  - _is_in_quiet_window: empty start OR empty end → False
  - _is_in_quiet_window: inside / outside a normal window
  - _is_in_quiet_window: wraparound (22:00 → 07:00) inside / outside
  - _is_in_quiet_window: malformed HH:MM strings → False
  - deliver_alert during quiet hours returns the "channel in quiet hours"
    failure result; CRITICAL with override=True still delivers;
    CRITICAL with override=False is suppressed
  - deliver_alert outside quiet hours behaves as today
  - Pre-v13 rows load with the dataclass defaults (column DEFAULTs
    preserve back-compat)
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from engine import alert_delivery
from engine.alert_delivery import (
    DeliveryChannel,
    _is_in_quiet_window,
    _parse_hhmm_to_minutes,
    deliver_alert,
    load_channels,
    save_channel,
)
from engine.alert_engine_v2 import ShippingAlert


# ─── Fixture: isolate SQLite DB per test ──────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ──────────────────────────────────────────────────────────────

def _make_alert(severity: str = "HIGH", *, alert_id: str = "a1") -> ShippingAlert:
    return ShippingAlert(
        alert_id=alert_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        alert_type="STOCK_MOVE",
        severity=severity,
        title=f"{severity} alert",
        body="Body",
        ticker="ZIM",
        route_id="",
        port_locode="",
        value=1.0,
        threshold=0.5,
        change_pct=100.0,
        acknowledged=False,
    )


def _make_channel(
    *,
    channel_id: str = "ch-q1",
    kind: str = "slack",
    target: str = "https://hooks.slack.com/services/T000/B000/XXXX",
    quiet_start: str = "",
    quiet_end: str = "",
    quiet_override_critical: bool = True,
) -> DeliveryChannel:
    return DeliveryChannel(
        channel_id=channel_id,
        name="Q channel",
        kind=kind,
        target=target,
        severity_threshold="LOW",
        enabled=True,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
        quiet_override_critical=quiet_override_critical,
    )


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text


# ─── Dataclass defaults ───────────────────────────────────────────────────

def test_delivery_channel_defaults_quiet_fields_to_disabled() -> None:
    """A bare DeliveryChannel must default to no quiet window configured
    and the safe CRITICAL-override = True."""
    ch = DeliveryChannel(
        channel_id="c",
        name="n",
        kind="slack",
        target="https://example.com",
        severity_threshold="LOW",
    )
    assert ch.quiet_start == ""
    assert ch.quiet_end == ""
    assert ch.quiet_override_critical is True


def test_delivery_channel_accepts_quiet_field_values() -> None:
    ch = _make_channel(quiet_start="22:00", quiet_end="07:00", quiet_override_critical=False)
    assert ch.quiet_start == "22:00"
    assert ch.quiet_end == "07:00"
    assert ch.quiet_override_critical is False


# ─── _parse_hhmm_to_minutes ───────────────────────────────────────────────

def test_parse_hhmm_basic() -> None:
    assert _parse_hhmm_to_minutes("00:00") == 0
    assert _parse_hhmm_to_minutes("01:30") == 90
    assert _parse_hhmm_to_minutes("22:00") == 22 * 60
    assert _parse_hhmm_to_minutes("23:59") == 23 * 60 + 59


def test_parse_hhmm_unpadded() -> None:
    """Single-digit hours are accepted ("7:30" == "07:30")."""
    assert _parse_hhmm_to_minutes("7:30") == 7 * 60 + 30


def test_parse_hhmm_rejects_out_of_range() -> None:
    assert _parse_hhmm_to_minutes("24:00") is None
    assert _parse_hhmm_to_minutes("12:60") is None
    assert _parse_hhmm_to_minutes("-1:00") is None


def test_parse_hhmm_rejects_garbage() -> None:
    assert _parse_hhmm_to_minutes("") is None
    assert _parse_hhmm_to_minutes("garbage") is None
    assert _parse_hhmm_to_minutes("12") is None
    assert _parse_hhmm_to_minutes("12:30:45") is None
    assert _parse_hhmm_to_minutes(None) is None  # type: ignore[arg-type]


# ─── _is_in_quiet_window: empty fields ────────────────────────────────────

def test_is_in_quiet_window_empty_start_returns_false() -> None:
    ch = _make_channel(quiet_start="", quiet_end="07:00")
    now = datetime(2026, 5, 21, 3, 0, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is False


def test_is_in_quiet_window_empty_end_returns_false() -> None:
    ch = _make_channel(quiet_start="22:00", quiet_end="")
    now = datetime(2026, 5, 21, 3, 0, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is False


def test_is_in_quiet_window_both_empty_returns_false() -> None:
    ch = _make_channel(quiet_start="", quiet_end="")
    now = datetime(2026, 5, 21, 3, 0, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is False


# ─── _is_in_quiet_window: normal (same-day) window ────────────────────────

def test_is_in_quiet_window_normal_inside_returns_true() -> None:
    """10:00 → 12:00, now=11:30 → True."""
    ch = _make_channel(quiet_start="10:00", quiet_end="12:00")
    now = datetime(2026, 5, 21, 11, 30, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is True


def test_is_in_quiet_window_normal_at_start_is_inside() -> None:
    """At the exact start time the window is considered active
    (half-open interval [start, end))."""
    ch = _make_channel(quiet_start="10:00", quiet_end="12:00")
    now = datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is True


def test_is_in_quiet_window_normal_at_end_is_outside() -> None:
    """At the exact end time the window has just closed (half-open)."""
    ch = _make_channel(quiet_start="10:00", quiet_end="12:00")
    now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is False


def test_is_in_quiet_window_normal_outside_returns_false() -> None:
    ch = _make_channel(quiet_start="10:00", quiet_end="12:00")
    now = datetime(2026, 5, 21, 9, 30, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is False
    now = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is False


# ─── _is_in_quiet_window: wraparound ──────────────────────────────────────

def test_is_in_quiet_window_wraparound_after_start_returns_true() -> None:
    """22:00 → 07:00, now=23:30 → True (past start, before midnight)."""
    ch = _make_channel(quiet_start="22:00", quiet_end="07:00")
    now = datetime(2026, 5, 21, 23, 30, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is True


def test_is_in_quiet_window_wraparound_before_end_returns_true() -> None:
    """22:00 → 07:00, now=03:00 → True (after midnight, before end)."""
    ch = _make_channel(quiet_start="22:00", quiet_end="07:00")
    now = datetime(2026, 5, 21, 3, 0, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is True


def test_is_in_quiet_window_wraparound_outside_returns_false() -> None:
    """22:00 → 07:00, now=12:00 → False."""
    ch = _make_channel(quiet_start="22:00", quiet_end="07:00")
    now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is False


def test_is_in_quiet_window_wraparound_at_end_is_outside() -> None:
    """At the exact end (07:00), the window has just closed."""
    ch = _make_channel(quiet_start="22:00", quiet_end="07:00")
    now = datetime(2026, 5, 21, 7, 0, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is False


# ─── _is_in_quiet_window: defensive parsing ───────────────────────────────

def test_is_in_quiet_window_malformed_start_returns_false() -> None:
    ch = _make_channel(quiet_start="garbage", quiet_end="07:00")
    now = datetime(2026, 5, 21, 3, 0, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is False


def test_is_in_quiet_window_malformed_end_returns_false() -> None:
    ch = _make_channel(quiet_start="22:00", quiet_end="25:99")
    now = datetime(2026, 5, 21, 3, 0, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is False


def test_is_in_quiet_window_zero_duration_returns_false() -> None:
    """A start == end window has zero duration. Better to treat as "not
    configured" than as a 24h silence (the user might have half-edited
    the form)."""
    ch = _make_channel(quiet_start="10:00", quiet_end="10:00")
    now = datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc)
    assert _is_in_quiet_window(ch, now) is False


# ─── deliver_alert during / outside quiet hours ───────────────────────────

def _freeze_now(monkeypatch, fixed: datetime) -> None:
    """Monkeypatch datetime.now used inside engine.alert_delivery so the
    quiet-hours check sees ``fixed`` instead of wall-clock time."""

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(alert_delivery, "datetime", _FrozenDatetime)


def test_deliver_alert_during_quiet_hours_returns_quiet_hours_failure(monkeypatch) -> None:
    """A non-CRITICAL alert fired during the quiet window returns the
    "channel in quiet hours" failure shape — no HTTP exchange happens."""
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    _freeze_now(monkeypatch, datetime(2026, 5, 21, 3, 0, tzinfo=timezone.utc))

    ch = _make_channel(quiet_start="22:00", quiet_end="07:00")
    result = deliver_alert(_make_alert("HIGH"), ch)
    assert result.success is False
    assert result.status_code == 0
    assert "quiet hours" in result.error_msg
    assert called["n"] == 0


def test_deliver_alert_during_quiet_hours_critical_overrides(monkeypatch) -> None:
    """CRITICAL + override=True bypasses the quiet window and delivers
    normally (HTTP 200)."""
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    _freeze_now(monkeypatch, datetime(2026, 5, 21, 3, 0, tzinfo=timezone.utc))

    ch = _make_channel(
        quiet_start="22:00", quiet_end="07:00", quiet_override_critical=True
    )
    result = deliver_alert(_make_alert("CRITICAL"), ch)
    assert result.success is True
    assert called["n"] == 1


def test_deliver_alert_during_quiet_hours_critical_no_override_is_suppressed(monkeypatch) -> None:
    """CRITICAL + override=False is still suppressed inside the window."""
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    _freeze_now(monkeypatch, datetime(2026, 5, 21, 3, 0, tzinfo=timezone.utc))

    ch = _make_channel(
        quiet_start="22:00", quiet_end="07:00", quiet_override_critical=False
    )
    result = deliver_alert(_make_alert("CRITICAL"), ch)
    assert result.success is False
    assert "quiet hours" in result.error_msg
    assert called["n"] == 0


def test_deliver_alert_outside_quiet_hours_delivers_normally(monkeypatch) -> None:
    """Outside the configured window, behaviour is identical to a channel
    with no quiet hours."""
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    # 12:00 is well outside 22:00 → 07:00.
    _freeze_now(monkeypatch, datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc))

    ch = _make_channel(quiet_start="22:00", quiet_end="07:00")
    result = deliver_alert(_make_alert("HIGH"), ch)
    assert result.success is True
    assert called["n"] == 1


def test_deliver_alert_unconfigured_quiet_hours_delivers_normally(monkeypatch) -> None:
    """A channel with no quiet window configured behaves exactly as today
    regardless of wall-clock time."""
    called = {"n": 0}

    def fake_post(*a, **kw):
        called["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)
    _freeze_now(monkeypatch, datetime(2026, 5, 21, 3, 0, tzinfo=timezone.utc))

    ch = _make_channel(quiet_start="", quiet_end="")
    result = deliver_alert(_make_alert("HIGH"), ch)
    assert result.success is True
    assert called["n"] == 1


# ─── Persistence: round-trip + back-compat ────────────────────────────────

def test_save_channel_round_trips_quiet_fields_for_slack() -> None:
    """A slack channel with quiet hours saved + loaded must round-trip
    every field — including alongside the existing digest_mode."""
    ch = _make_channel(
        channel_id="c-rt", quiet_start="22:00", quiet_end="07:00",
        quiet_override_critical=False,
    )
    ch.digest_mode = "daily"
    save_channel(ch)
    loaded = load_channels()
    assert len(loaded) == 1
    got = loaded[0]
    assert got.quiet_start == "22:00"
    assert got.quiet_end == "07:00"
    assert got.quiet_override_critical is False
    assert got.digest_mode == "daily"


def test_save_channel_round_trips_quiet_fields_for_email() -> None:
    """Same round-trip but for an email channel — confirms the
    persistence layer is kind-agnostic."""
    ch = _make_channel(
        channel_id="c-em", kind="email", target="ops@example.com",
        quiet_start="00:00", quiet_end="06:00",
    )
    save_channel(ch)
    loaded = load_channels()
    assert len(loaded) == 1
    got = loaded[0]
    assert got.kind == "email"
    assert got.target == "ops@example.com"
    assert got.quiet_start == "00:00"
    assert got.quiet_end == "06:00"
    assert got.quiet_override_critical is True


def test_save_channel_defaults_persisted_when_unconfigured() -> None:
    """A channel saved without quiet hours round-trips back as
    quiet_start='', quiet_end='', quiet_override_critical=True (the
    dataclass defaults)."""
    ch = _make_channel(channel_id="c-def")
    save_channel(ch)
    loaded = load_channels()[0]
    assert loaded.quiet_start == ""
    assert loaded.quiet_end == ""
    assert loaded.quiet_override_critical is True


def test_save_channel_drops_malformed_quiet_strings() -> None:
    """A stale/invalid HH:MM string in the dataclass must not poison
    the DB — save_channel coerces unparseable values to ''."""
    ch = _make_channel(channel_id="c-bad", quiet_start="not-a-time", quiet_end="07:00")
    save_channel(ch)
    loaded = load_channels()[0]
    # The malformed start was dropped to '' — the partial end is allowed
    # through, but _is_in_quiet_window will short-circuit to False.
    assert loaded.quiet_start == ""
    assert loaded.quiet_end == "07:00"


def test_save_channel_upsert_updates_quiet_fields() -> None:
    """A second save_channel for the same channel_id must overwrite the
    quiet-hours columns (same UPSERT shape as digest_mode)."""
    ch = _make_channel(channel_id="c-up", quiet_start="22:00", quiet_end="07:00")
    save_channel(ch)
    # Now save the same channel_id with different quiet hours
    ch2 = _make_channel(
        channel_id="c-up", quiet_start="01:00", quiet_end="05:00",
        quiet_override_critical=False,
    )
    save_channel(ch2)
    loaded = load_channels()
    assert len(loaded) == 1
    assert loaded[0].quiet_start == "01:00"
    assert loaded[0].quiet_end == "05:00"
    assert loaded[0].quiet_override_critical is False


def test_load_channels_defaults_for_pre_v13_rows() -> None:
    """A row inserted WITHOUT specifying the three v13 columns must come
    back with the column DEFAULTs — '' / '' / True — preserving the
    legacy "no quiet hours configured" behaviour."""
    from state.db import get_connection

    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO delivery_channels
              (channel_id, name, kind, target, severity_threshold,
               enabled, created_at)
            VALUES ('c-pre-v13', 'Legacy', 'slack',
                    'https://hooks.slack.com/services/x',
                    'HIGH', 1, '2026-05-21T00:00:00+00:00')
            """
        )
    loaded = load_channels()
    assert len(loaded) == 1
    got = loaded[0]
    assert got.quiet_start == ""
    assert got.quiet_end == ""
    assert got.quiet_override_critical is True
