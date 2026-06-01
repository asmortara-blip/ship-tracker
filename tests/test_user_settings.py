"""Tests for ``auth.settings`` — the v15 per-user preferences layer.

Exercises the :class:`UserSettings` dataclass shape, the three public
helpers (:func:`get_settings` / :func:`save_settings` /
:func:`update_setting`), and the audit-hook integration.

The defining properties under test:

  * :func:`get_settings` returns a fully-populated :class:`UserSettings`
    for ANY user (known or unknown) and NEVER raises.
  * :func:`save_settings` round-trips with :func:`get_settings`.
  * :func:`update_setting` mutates exactly one key and preserves the
    rest.
  * Invalid timezones and invalid themes coerce to ``"UTC"`` /
    ``"auto"`` rather than raising or persisting garbage.
  * :func:`save_settings` swallows a malformed input and returns
    ``False`` rather than raising.
  * The ``extras`` dict round-trips arbitrary JSON-serializable
    payloads.
  * Per-user isolation: alice's settings never leak into bob's.
  * The audit hook fires with the changed-keys LIST in the detail
    payload — never the values themselves.
"""
from __future__ import annotations

import json

import pytest


# ─── Fixture: isolate persistence to a tmp file per test ────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── UserSettings dataclass shape + defaults ─────────────────────────────

def test_usersettings_dataclass_fields() -> None:
    """The dataclass exposes exactly the six documented fields."""
    from auth.settings import UserSettings

    fields = set(UserSettings.__dataclass_fields__)
    assert fields == {
        "user_id", "timezone", "theme",
        "default_report_window_days", "default_alert_severity", "extras",
    }


def test_usersettings_defaults_match_module_constants() -> None:
    """Construct with only user_id → all the documented defaults land."""
    from auth.settings import UserSettings

    s = UserSettings(user_id="u-alice")
    assert s.user_id == "u-alice"
    assert s.timezone == "UTC"
    assert s.theme == "auto"
    assert s.default_report_window_days == 30
    assert s.default_alert_severity == "LOW"
    assert s.extras == {}


def test_usersettings_extras_default_is_independent_per_instance() -> None:
    """A common mutable-default footgun — confirm we used ``field(default_factory=dict)``."""
    from auth.settings import UserSettings

    a = UserSettings(user_id="a")
    b = UserSettings(user_id="b")
    a.extras["foo"] = "bar"
    assert b.extras == {}


# ─── get_settings: unknown user returns defaults ─────────────────────────

def test_get_settings_unknown_user_returns_defaults() -> None:
    """Pre-v15 / never-saved users get a fresh defaults dataclass."""
    from auth.settings import get_settings

    s = get_settings("never-saved")
    assert s.user_id == "never-saved"
    assert s.timezone == "UTC"
    assert s.theme == "auto"
    assert s.default_report_window_days == 30
    assert s.default_alert_severity == "LOW"
    assert s.extras == {}


def test_get_settings_never_raises_on_db_error() -> None:
    """A DB failure must NOT propagate — returns the defaults instead."""
    from unittest.mock import patch

    from auth import settings as settings_mod

    with patch("state.db.get_connection", side_effect=RuntimeError("disk full")):
        result = settings_mod.get_settings("u1")
    # Must come back as the defaults dataclass — never raises.
    assert result.user_id == "u1"
    assert result.timezone == "UTC"


def test_get_settings_handles_non_string_user_id() -> None:
    """A non-string user_id falls back to empty-string id + defaults."""
    from auth.settings import get_settings

    s = get_settings(None)  # type: ignore[arg-type]
    assert s.user_id == ""
    assert s.timezone == "UTC"


# ─── save + get round-trip ───────────────────────────────────────────────

def test_save_then_get_roundtrip() -> None:
    from auth.settings import UserSettings, get_settings, save_settings

    s = UserSettings(
        user_id="u-roundtrip",
        timezone="America/New_York",
        theme="dark",
        default_report_window_days=14,
        default_alert_severity="HIGH",
        extras={"chart_density": "compact", "fav_tabs": ["overview", "alerts"]},
    )
    assert save_settings(s) is True

    fetched = get_settings("u-roundtrip")
    assert fetched.user_id == "u-roundtrip"
    assert fetched.timezone == "America/New_York"
    assert fetched.theme == "dark"
    assert fetched.default_report_window_days == 14
    assert fetched.default_alert_severity == "HIGH"
    assert fetched.extras == {"chart_density": "compact", "fav_tabs": ["overview", "alerts"]}


def test_save_settings_replaces_existing_row() -> None:
    """A second save for the same user OVERWRITES, not duplicates."""
    from auth.settings import UserSettings, get_settings, save_settings
    from state.db import get_connection

    save_settings(UserSettings(user_id="u1", theme="dark"))
    save_settings(UserSettings(user_id="u1", theme="light"))

    conn = get_connection()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM user_settings WHERE user_id = 'u1'"
    ).fetchone()["n"]
    assert n == 1
    assert get_settings("u1").theme == "light"


def test_save_settings_stamps_updated_at() -> None:
    """The ``updated_at`` column gets an ISO-8601 UTC timestamp on save."""
    from datetime import datetime, timezone

    from auth.settings import UserSettings, save_settings
    from state.db import get_connection

    save_settings(UserSettings(user_id="u1"))
    conn = get_connection()
    row = conn.execute(
        "SELECT updated_at FROM user_settings WHERE user_id = 'u1'"
    ).fetchone()
    parsed = datetime.fromisoformat(row["updated_at"])
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert delta < 60


# ─── update_setting: changes ONE key, preserves others ───────────────────

def test_update_setting_changes_one_key_preserves_others() -> None:
    from auth.settings import UserSettings, get_settings, save_settings, update_setting

    save_settings(UserSettings(
        user_id="u-upd",
        timezone="America/New_York",
        theme="dark",
        default_report_window_days=14,
        default_alert_severity="HIGH",
        extras={"k": "v"},
    ))
    assert update_setting("u-upd", "timezone", "Europe/London") is True

    fetched = get_settings("u-upd")
    assert fetched.timezone == "Europe/London"   # changed
    assert fetched.theme == "dark"                # preserved
    assert fetched.default_report_window_days == 14
    assert fetched.default_alert_severity == "HIGH"
    assert fetched.extras == {"k": "v"}


def test_update_setting_unknown_key_lands_in_extras() -> None:
    """An unknown key goes into the ``extras`` dict — the zero-bump
    extension point."""
    from auth.settings import get_settings, update_setting

    assert update_setting("u-extras", "favorite_tab", "overview") is True
    fetched = get_settings("u-extras")
    assert fetched.extras == {"favorite_tab": "overview"}


def test_update_setting_on_unknown_user_creates_row() -> None:
    """Calling update_setting on a user with no row creates one populated
    with the defaults + the updated key."""
    from auth.settings import get_settings, update_setting

    assert update_setting("u-new", "theme", "dark") is True
    fetched = get_settings("u-new")
    assert fetched.theme == "dark"
    assert fetched.timezone == "UTC"  # default preserved


def test_update_setting_never_raises_on_bad_input() -> None:
    """Empty user_id / non-string key → False, never raises."""
    from auth.settings import update_setting

    assert update_setting("", "theme", "dark") is False
    assert update_setting("u1", "", "x") is False
    assert update_setting(None, "theme", "dark") is False  # type: ignore[arg-type]


# ─── Defensive coercion ──────────────────────────────────────────────────

def test_save_invalid_timezone_coerces_to_utc() -> None:
    from auth.settings import UserSettings, get_settings, save_settings

    assert save_settings(UserSettings(user_id="u-tz", timezone="Mars/Olympus")) is True
    fetched = get_settings("u-tz")
    assert fetched.timezone == "UTC"


def test_save_invalid_theme_coerces_to_auto() -> None:
    from auth.settings import UserSettings, get_settings, save_settings

    assert save_settings(UserSettings(user_id="u-th", theme="neon")) is True
    fetched = get_settings("u-th")
    assert fetched.theme == "auto"


def test_save_invalid_severity_coerces_to_low() -> None:
    from auth.settings import UserSettings, get_settings, save_settings

    assert save_settings(
        UserSettings(user_id="u-sev", default_alert_severity="EXTREME")
    ) is True
    fetched = get_settings("u-sev")
    assert fetched.default_alert_severity == "LOW"


def test_save_non_positive_window_coerces_to_default() -> None:
    from auth.settings import UserSettings, get_settings, save_settings

    assert save_settings(
        UserSettings(user_id="u-w", default_report_window_days=0)
    ) is True
    assert get_settings("u-w").default_report_window_days == 30

    assert save_settings(
        UserSettings(user_id="u-w2", default_report_window_days=-7)
    ) is True
    assert get_settings("u-w2").default_report_window_days == 30


# ─── save_settings: never raises ─────────────────────────────────────────

def test_save_settings_non_usersettings_input_returns_false() -> None:
    """A wrong-type input (dict, None, list) → False, never raises."""
    from auth.settings import save_settings

    assert save_settings({"user_id": "u1"}) is False  # type: ignore[arg-type]
    assert save_settings(None) is False  # type: ignore[arg-type]
    assert save_settings([]) is False  # type: ignore[arg-type]


def test_save_settings_missing_user_id_returns_false() -> None:
    from auth.settings import UserSettings, save_settings

    assert save_settings(UserSettings(user_id="")) is False
    # Build a hand-crafted bad instance without user_id type-checking
    bad = UserSettings(user_id="x")
    bad.user_id = None  # type: ignore[assignment]
    assert save_settings(bad) is False


def test_save_settings_never_raises_on_db_failure() -> None:
    """An underlying DB exception is swallowed → returns False."""
    from unittest.mock import patch

    from auth.settings import UserSettings, save_settings

    with patch("state.db.get_connection", side_effect=RuntimeError("disk on fire")):
        result = save_settings(UserSettings(user_id="u1"))
    assert result is False


# ─── extras dict round-trip ──────────────────────────────────────────────

def test_extras_roundtrips_nested_and_mixed_types() -> None:
    """The ``extras`` blob must survive complex JSON-serializable payloads."""
    from auth.settings import UserSettings, get_settings, save_settings

    extras = {
        "favorite_tabs": ["overview", "alerts", "deep_dive"],
        "chart_settings": {"density": "compact", "show_grid": True},
        "thresholds": {"bdi_high": 2500, "bdi_low": 1200},
        "boolean_pref": False,
        "null_pref": None,
    }
    assert save_settings(UserSettings(user_id="u-ex", extras=extras)) is True
    fetched = get_settings("u-ex")
    assert fetched.extras == extras


def test_extras_non_dict_coerces_to_empty() -> None:
    """A non-dict extras (a stray list / None) coerces to {} not raises."""
    from auth.settings import UserSettings, get_settings, save_settings

    bad = UserSettings(user_id="u-ex2")
    bad.extras = ["not", "a", "dict"]  # type: ignore[assignment]
    assert save_settings(bad) is True
    assert get_settings("u-ex2").extras == {}


# ─── Per-user isolation ──────────────────────────────────────────────────

def test_per_user_isolation() -> None:
    """alice's settings do NOT bleed into bob's, and vice versa."""
    from auth.settings import UserSettings, get_settings, save_settings

    save_settings(UserSettings(
        user_id="alice",
        timezone="America/New_York",
        theme="dark",
        default_alert_severity="HIGH",
    ))
    save_settings(UserSettings(
        user_id="bob",
        timezone="Europe/London",
        theme="light",
        default_alert_severity="CRITICAL",
    ))

    alice = get_settings("alice")
    bob = get_settings("bob")

    assert alice.timezone == "America/New_York"
    assert bob.timezone == "Europe/London"
    assert alice.theme == "dark"
    assert bob.theme == "light"
    assert alice.default_alert_severity == "HIGH"
    assert bob.default_alert_severity == "CRITICAL"

    # Updating alice's prefs must not touch bob's.
    save_settings(UserSettings(
        user_id="alice",
        timezone="Asia/Tokyo",
        theme="auto",
    ))
    assert get_settings("alice").timezone == "Asia/Tokyo"
    assert get_settings("bob").timezone == "Europe/London"


# ─── Audit hook ──────────────────────────────────────────────────────────

def test_audit_hook_fires_with_changed_keys() -> None:
    """save_settings records an audit event whose ``detail.keys_changed``
    lists the changed top-level keys."""
    from auth.audit import query_audit
    from auth.settings import UserSettings, save_settings

    # First save — every non-default field changes vs the defaults.
    save_settings(UserSettings(
        user_id="u-audit",
        timezone="America/New_York",
        theme="dark",
        default_report_window_days=14,
        default_alert_severity="HIGH",
        extras={"k": "v"},
    ))
    rows = query_audit(action="save_user_settings")
    assert len(rows) == 1
    assert rows[0].entity_type == "user_settings"
    assert rows[0].entity_id == "u-audit"
    keys = set(rows[0].detail_json["keys_changed"])
    # All five top-level fields changed vs the defaults.
    assert keys == {
        "timezone", "theme",
        "default_report_window_days", "default_alert_severity",
        "extras",
    }


def test_audit_hook_only_lists_actually_changed_keys() -> None:
    """A second save with only ONE field different lists only that key."""
    from auth.audit import query_audit
    from auth.settings import UserSettings, save_settings

    save_settings(UserSettings(
        user_id="u-aud2",
        timezone="America/New_York",
        theme="dark",
    ))
    # Second save: keep theme the same, change ONLY the timezone.
    save_settings(UserSettings(
        user_id="u-aud2",
        timezone="Europe/London",
        theme="dark",
    ))
    # Two audit rows total.
    rows = query_audit(action="save_user_settings", user_id="u-aud2")
    assert len(rows) == 2
    # rows are newest-first; the most-recent one should show only
    # "timezone" as changed.
    most_recent = rows[0]
    assert most_recent.detail_json["keys_changed"] == ["timezone"]


def test_audit_hook_does_not_leak_values() -> None:
    """The audit payload must carry ONLY the changed-key list, never
    the values (timezone string, theme name, etc.)."""
    from auth.audit import query_audit
    from auth.settings import UserSettings, save_settings

    save_settings(UserSettings(
        user_id="u-leak",
        timezone="America/New_York",
        theme="dark",
        extras={"secret_pref": "do-not-leak-me"},
    ))
    rows = query_audit(action="save_user_settings", user_id="u-leak")
    encoded = json.dumps(rows[0].detail_json)
    # None of the actual values should appear in the audit detail.
    assert "America/New_York" not in encoded
    assert "dark" not in encoded
    assert "do-not-leak-me" not in encoded
    # The keys_changed list should be present.
    assert "keys_changed" in rows[0].detail_json


def test_audit_hook_user_id_matches_settings_user_id() -> None:
    """The audit row's user_id column equals the settings' user_id (not
    the empty-string session default)."""
    from auth.audit import query_audit
    from auth.settings import UserSettings, save_settings

    save_settings(UserSettings(user_id="u-stamp", theme="dark"))
    rows = query_audit(action="save_user_settings")
    assert len(rows) == 1
    assert rows[0].user_id == "u-stamp"
