"""Tests for ``utils.tz`` — per-user timezone formatting helpers.

The four public helpers
(:func:`get_user_timezone`, :func:`to_user_tz`, :func:`format_user_tz`,
:func:`now_in_user_tz`) MUST never raise. Every failure path collapses
to a safe default:

  * ``get_user_timezone`` → ``"UTC"``
  * ``to_user_tz`` → input returned unchanged
  * ``format_user_tz`` → ``""``
  * ``now_in_user_tz`` → ``datetime.now(timezone.utc)``

These tests exercise the success paths AND each failure path. The
SQLite-backed ``isolated_state_db`` fixture mirrors the pattern in
``tests/test_user_settings.py`` so each test gets its own per-test DB
when it persists a ``UserSettings`` row.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest


# ─── Fixture: per-test SQLite isolation ────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp path so a test
    that persists ``UserSettings`` does not touch the real cache DB."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── get_user_timezone ────────────────────────────────────────────────────

def test_get_user_timezone_empty_user_returns_utc():
    """When ``current_user_id`` returns the empty string, helper returns UTC."""
    from utils import tz as tz_mod

    with patch.object(tz_mod, "_resolve_user_id", return_value=""):
        assert tz_mod.get_user_timezone() == "UTC"


def test_get_user_timezone_explicit_empty_user_id_returns_utc():
    """An explicit empty-string user_id also collapses to UTC."""
    from utils.tz import get_user_timezone

    assert get_user_timezone(user_id="") == "UTC"


def test_get_user_timezone_returns_saved_timezone():
    """A user with timezone=America/New_York in user_settings → that string."""
    from auth.settings import UserSettings, save_settings
    from utils.tz import get_user_timezone

    save_settings(UserSettings(user_id="u-tz", timezone="America/New_York"))
    assert get_user_timezone(user_id="u-tz") == "America/New_York"


def test_get_user_timezone_unknown_user_returns_utc_default():
    """A user with no saved settings → defaults UserSettings.timezone='UTC'."""
    from utils.tz import get_user_timezone

    assert get_user_timezone(user_id="never-saved") == "UTC"


def test_get_user_timezone_never_raises_when_settings_throws():
    """Any exception inside ``auth.settings.get_settings`` collapses to UTC."""
    from utils import tz as tz_mod

    def _boom(_user_id):
        raise RuntimeError("settings unavailable")

    with patch("auth.settings.get_settings", side_effect=_boom):
        # Pass an explicit user_id so we exercise the settings call path.
        assert tz_mod.get_user_timezone(user_id="u-x") == "UTC"


def test_get_user_timezone_handles_non_string_timezone_attribute():
    """A corrupted settings row whose timezone is not a string → UTC."""
    from utils import tz as tz_mod

    class _BadSettings:
        timezone = 42  # not a string

    with patch("auth.settings.get_settings", return_value=_BadSettings()):
        assert tz_mod.get_user_timezone(user_id="u-bad") == "UTC"


# ─── to_user_tz ───────────────────────────────────────────────────────────

def test_to_user_tz_converts_aware_datetime_to_user_zone():
    """A UTC datetime + user_tz=America/New_York → datetime in EST/EDT."""
    from auth.settings import UserSettings, save_settings
    from utils.tz import to_user_tz

    save_settings(UserSettings(user_id="u-ny", timezone="America/New_York"))

    # Pick a deterministic UTC moment.
    dt_utc = datetime(2026, 5, 22, 13, 15, 0, tzinfo=timezone.utc)
    out = to_user_tz(dt_utc, user_id="u-ny")
    assert isinstance(out, datetime)
    assert out.tzinfo is not None
    # America/New_York is EDT in May → UTC-4.
    assert out.utcoffset().total_seconds() == -4 * 3600
    # The wall-clock should land at 09:15 EDT.
    assert (out.year, out.month, out.day) == (2026, 5, 22)
    assert (out.hour, out.minute) == (9, 15)


def test_to_user_tz_parses_iso_string():
    """An ISO 8601 UTC string is parsed and converted to the user's TZ."""
    from auth.settings import UserSettings, save_settings
    from utils.tz import to_user_tz

    save_settings(UserSettings(user_id="u-tok", timezone="Asia/Tokyo"))

    out = to_user_tz("2026-05-22T00:00:00+00:00", user_id="u-tok")
    assert isinstance(out, datetime)
    # Asia/Tokyo is UTC+9 → midnight UTC = 09:00 JST same day.
    assert (out.hour, out.minute) == (9, 0)


def test_to_user_tz_handles_z_suffix_iso():
    """The trailing 'Z' suffix common in ISO timestamps is handled."""
    from utils.tz import to_user_tz

    out = to_user_tz("2026-05-22T13:15:00Z", user_id="")  # empty → UTC
    assert isinstance(out, datetime)
    assert out.utcoffset().total_seconds() == 0
    assert (out.hour, out.minute) == (13, 15)


def test_to_user_tz_unparseable_string_returns_input():
    """A garbage string is returned unchanged (no exception)."""
    from utils.tz import to_user_tz

    bad = "definitely not an ISO 8601 string"
    out = to_user_tz(bad, user_id="")
    assert out == bad


def test_to_user_tz_invalid_user_tz_returns_utc_datetime():
    """If the user's saved tz is invalid, helper returns the UTC datetime
    (NOT the original input — the parse succeeded, only the zone lookup
    failed). The datetime is still timezone-aware and in UTC."""
    from utils import tz as tz_mod

    dt_utc = datetime(2026, 5, 22, 13, 15, 0, tzinfo=timezone.utc)
    # Patch get_user_timezone to return a bogus IANA name.
    with patch.object(tz_mod, "get_user_timezone", return_value="Mars/Olympus"):
        out = tz_mod.to_user_tz(dt_utc, user_id="u-bogus")
    assert isinstance(out, datetime)
    assert out.tzinfo == timezone.utc


def test_to_user_tz_naive_datetime_treated_as_utc():
    """A naive datetime is assumed to be UTC — matches persistence convention."""
    from auth.settings import UserSettings, save_settings
    from utils.tz import to_user_tz

    save_settings(UserSettings(user_id="u-naive", timezone="Europe/London"))

    naive = datetime(2026, 1, 15, 12, 0, 0)  # naive Jan 15 noon (no tz)
    out = to_user_tz(naive, user_id="u-naive")
    assert isinstance(out, datetime)
    # London in January is UTC (GMT) → 12:00 London = 12:00 UTC.
    assert (out.hour, out.minute) == (12, 0)


def test_to_user_tz_garbage_input_type_returns_input():
    """Non-datetime, non-string input (e.g. int) is returned unchanged."""
    from utils.tz import to_user_tz

    assert to_user_tz(12345, user_id="") == 12345  # type: ignore[arg-type]


# ─── format_user_tz ───────────────────────────────────────────────────────

def test_format_user_tz_renders_iso_string():
    """A UTC ISO 8601 string + user TZ → strftime output in user zone."""
    from auth.settings import UserSettings, save_settings
    from utils.tz import format_user_tz

    save_settings(UserSettings(user_id="u-fmt", timezone="America/New_York"))

    out = format_user_tz(
        "2026-05-22T13:15:00+00:00",
        fmt="%Y-%m-%d %H:%M %Z",
        user_id="u-fmt",
    )
    # 13:15 UTC in May NY is 09:15 EDT.
    assert out.startswith("2026-05-22 09:15 ")
    assert "EDT" in out or "EST" in out  # zone abbrev present


def test_format_user_tz_returns_empty_on_unparseable_input():
    """A garbage timestamp cannot be formatted → empty string."""
    from utils.tz import format_user_tz

    assert format_user_tz("not a date", user_id="") == ""


def test_format_user_tz_empty_string_returns_empty():
    """Empty input string short-circuits to empty output."""
    from utils.tz import format_user_tz

    assert format_user_tz("", user_id="") == ""


# ─── now_in_user_tz ───────────────────────────────────────────────────────

def test_now_in_user_tz_returns_datetime_in_user_zone():
    """now_in_user_tz returns an aware datetime in the user's TZ."""
    from auth.settings import UserSettings, save_settings
    from utils.tz import now_in_user_tz

    save_settings(UserSettings(user_id="u-now", timezone="Asia/Tokyo"))

    out = now_in_user_tz(user_id="u-now")
    assert isinstance(out, datetime)
    assert out.tzinfo is not None
    # Tokyo offset is +09:00 year-round.
    assert out.utcoffset() == ZoneInfo("Asia/Tokyo").utcoffset(out)


def test_now_in_user_tz_falls_back_to_utc_on_failure():
    """If to_user_tz somehow returns a non-datetime, fallback is UTC now."""
    from utils import tz as tz_mod

    # to_user_tz returning the raw input (str) is the failure signal.
    with patch.object(tz_mod, "to_user_tz", return_value="not a dt"):
        out = tz_mod.now_in_user_tz(user_id="u-x")
    assert isinstance(out, datetime)
    assert out.tzinfo == timezone.utc
