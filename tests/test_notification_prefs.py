"""Tests for ``auth.notification_prefs`` — per-user notification preferences.

Storage is a single JSON blob per user under
``kv_state['notification_prefs:<user_id>']``. Filter rules are applied in
a documented order; CRITICAL bypasses quiet hours. Every helper NEVER
raises so a corrupted blob can't silently drop deliveries.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pytest

from auth.notification_prefs import (
    NotificationPrefs,
    filter_channels_by_prefs,
    get_prefs,
    get_suppressed_by_prefs_count,
    reset_prefs,
    save_prefs,
    update_pref,
)


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite isolation — every helper hits this scratch DB."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Stub helpers ─────────────────────────────────────────────────────────


@dataclass
class _Alert:
    severity: str = "MEDIUM"
    alert_type: str = "BDI_MOVE"


@dataclass
class _Channel:
    channel_id: str = "ch-a"
    name: str = "test"
    kind: str = "slack"
    target: str = "https://example.com/wh"
    severity_threshold: str = "LOW"
    enabled: bool = True


# ─── get / save / update / reset round-trip ───────────────────────────────


def test_get_prefs_returns_defaults_for_new_user() -> None:
    p = get_prefs(user_id="alice")
    assert isinstance(p, NotificationPrefs)
    assert p.enabled is True
    assert p.min_severity == "LOW"
    assert p.severity_channel_map == {}
    assert p.alert_type_filter == []
    assert p.quiet_during_hours is None


def test_save_prefs_and_reload_round_trips() -> None:
    p = NotificationPrefs(
        user_id="alice",
        enabled=False,
        min_severity="HIGH",
        severity_channel_map={"CRITICAL": ["ch-pager"]},
        alert_type_filter=["BDI_MOVE", "MACRO"],
        quiet_during_hours=(22, 6),
    )
    assert save_prefs(p, user_id="alice") is True
    reloaded = get_prefs(user_id="alice")
    assert reloaded.enabled is False
    assert reloaded.min_severity == "HIGH"
    assert reloaded.severity_channel_map == {"CRITICAL": ["ch-pager"]}
    assert reloaded.alert_type_filter == ["BDI_MOVE", "MACRO"]
    assert reloaded.quiet_during_hours == (22, 6)


def test_update_pref_partial_update_keeps_other_fields() -> None:
    # Seed with non-default values.
    save_prefs(NotificationPrefs(
        user_id="alice", min_severity="HIGH",
        alert_type_filter=["BDI_MOVE"],
    ), user_id="alice")
    # Update only min_severity.
    assert update_pref(user_id="alice", min_severity="CRITICAL") is True
    p = get_prefs(user_id="alice")
    assert p.min_severity == "CRITICAL"
    assert p.alert_type_filter == ["BDI_MOVE"]  # untouched


def test_reset_prefs_wipes_back_to_defaults() -> None:
    save_prefs(NotificationPrefs(
        user_id="alice", enabled=False, min_severity="HIGH",
    ), user_id="alice")
    assert reset_prefs(user_id="alice") is True
    p = get_prefs(user_id="alice")
    assert p.enabled is True
    assert p.min_severity == "LOW"


def test_per_user_isolation() -> None:
    save_prefs(NotificationPrefs(user_id="alice", min_severity="CRITICAL"), user_id="alice")
    save_prefs(NotificationPrefs(user_id="bob", min_severity="LOW"), user_id="bob")
    assert get_prefs(user_id="alice").min_severity == "CRITICAL"
    assert get_prefs(user_id="bob").min_severity == "LOW"


# ─── filter_channels_by_prefs — rule-by-rule ─────────────────────────────


def test_filter_returns_empty_when_disabled() -> None:
    save_prefs(NotificationPrefs(user_id="alice", enabled=False), user_id="alice")
    result = filter_channels_by_prefs([_Channel()], _Alert(), user_id="alice")
    assert result == []


def test_filter_returns_empty_when_below_min_severity() -> None:
    save_prefs(NotificationPrefs(user_id="alice", min_severity="HIGH"), user_id="alice")
    # MEDIUM < HIGH → suppressed
    result = filter_channels_by_prefs([_Channel()], _Alert(severity="MEDIUM"), user_id="alice")
    assert result == []


def test_filter_passes_when_meets_min_severity() -> None:
    save_prefs(NotificationPrefs(user_id="alice", min_severity="HIGH"), user_id="alice")
    result = filter_channels_by_prefs([_Channel()], _Alert(severity="HIGH"), user_id="alice")
    assert len(result) == 1


def test_filter_alert_type_filter_excludes_non_matching() -> None:
    save_prefs(NotificationPrefs(
        user_id="alice", alert_type_filter=["MACRO"],
    ), user_id="alice")
    result = filter_channels_by_prefs(
        [_Channel()], _Alert(alert_type="BDI_MOVE"), user_id="alice",
    )
    assert result == []


def test_filter_alert_type_filter_passes_matching() -> None:
    save_prefs(NotificationPrefs(
        user_id="alice", alert_type_filter=["BDI_MOVE"],
    ), user_id="alice")
    result = filter_channels_by_prefs(
        [_Channel()], _Alert(alert_type="BDI_MOVE"), user_id="alice",
    )
    assert len(result) == 1


def test_filter_empty_alert_type_filter_is_no_op() -> None:
    save_prefs(NotificationPrefs(
        user_id="alice", alert_type_filter=[],
    ), user_id="alice")
    result = filter_channels_by_prefs(
        [_Channel()], _Alert(alert_type="WHATEVER"), user_id="alice",
    )
    assert len(result) == 1


def test_filter_quiet_hours_suppresses_non_critical() -> None:
    save_prefs(NotificationPrefs(
        user_id="alice", quiet_during_hours=(0, 23),  # whole day
    ), user_id="alice")
    # Force the "current hour" to inside the window.
    now = datetime(2026, 5, 23, 5, 0, tzinfo=timezone.utc)
    result = filter_channels_by_prefs(
        [_Channel()], _Alert(severity="HIGH"), user_id="alice", now_utc=now,
    )
    assert result == []


def test_filter_quiet_hours_does_not_suppress_critical() -> None:
    save_prefs(NotificationPrefs(
        user_id="alice", quiet_during_hours=(0, 23),
    ), user_id="alice")
    now = datetime(2026, 5, 23, 5, 0, tzinfo=timezone.utc)
    result = filter_channels_by_prefs(
        [_Channel()], _Alert(severity="CRITICAL"), user_id="alice", now_utc=now,
    )
    assert len(result) == 1


def test_filter_quiet_hours_wrap_midnight() -> None:
    # Quiet 22:00 → 06:00 (the next day). 5 AM is inside the window.
    save_prefs(NotificationPrefs(
        user_id="alice", quiet_during_hours=(22, 6),
    ), user_id="alice")
    now = datetime(2026, 5, 23, 5, 0, tzinfo=timezone.utc)
    result = filter_channels_by_prefs(
        [_Channel()], _Alert(severity="HIGH"), user_id="alice", now_utc=now,
    )
    assert result == []
    # Noon is OUTSIDE the window.
    now = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    result = filter_channels_by_prefs(
        [_Channel()], _Alert(severity="HIGH"), user_id="alice", now_utc=now,
    )
    assert len(result) == 1


def test_filter_severity_channel_map_restricts_to_listed() -> None:
    save_prefs(NotificationPrefs(
        user_id="alice",
        severity_channel_map={"HIGH": ["ch-pager"]},
    ), user_id="alice")
    channels = [_Channel(channel_id="ch-slack"), _Channel(channel_id="ch-pager")]
    result = filter_channels_by_prefs(
        channels, _Alert(severity="HIGH"), user_id="alice",
    )
    assert len(result) == 1
    assert result[0].channel_id == "ch-pager"


def test_filter_severity_channel_map_no_entry_is_no_op() -> None:
    save_prefs(NotificationPrefs(
        user_id="alice", severity_channel_map={"CRITICAL": ["ch-pager"]},
    ), user_id="alice")
    # HIGH not in the map → no restriction.
    channels = [_Channel(channel_id="ch-slack"), _Channel(channel_id="ch-pager")]
    result = filter_channels_by_prefs(
        channels, _Alert(severity="HIGH"), user_id="alice",
    )
    assert len(result) == 2


# ─── Defensive / never-raises ─────────────────────────────────────────────


def test_filter_empty_channels_returns_empty() -> None:
    result = filter_channels_by_prefs([], _Alert(), user_id="alice")
    assert result == []


def test_filter_never_raises_on_garbage_alert() -> None:
    # An object missing severity / alert_type entirely.
    class _Garbage:
        pass
    result = filter_channels_by_prefs([_Channel()], _Garbage(), user_id="alice")
    # Returns SOMETHING valid (a list). Garbage severity defaults to
    # most-permissive in the prefs path.
    assert isinstance(result, list)


def test_save_prefs_with_invalid_severity_coerced_or_safe() -> None:
    # Invalid severity should be coerced or fallback to default — never
    # raise.
    p = NotificationPrefs(user_id="alice", min_severity="BANANA")
    result = save_prefs(p, user_id="alice")
    assert result in (True, False)
    # Read back — should be valid even though we wrote garbage.
    reloaded = get_prefs(user_id="alice")
    assert reloaded.min_severity in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


# ─── Suppressed counter ───────────────────────────────────────────────────


def test_suppressed_counter_starts_at_zero() -> None:
    assert get_suppressed_by_prefs_count() == 0


def test_suppressed_counter_helper_callable() -> None:
    # Counter is bumped by integration points outside the filter
    # (engine.alert_delivery records suppressions when it drops
    # channels because of prefs). Here we just verify the helper is
    # callable + returns a non-negative int.
    n = get_suppressed_by_prefs_count()
    assert isinstance(n, int)
    assert n >= 0
