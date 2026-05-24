"""Tests for engine.flap_detector (v19 anti-flap detection).

A metric oscillating around its threshold used to fire / resolve /
fire / resolve repeatedly. The flap detector watches the crossing
cadence and, once a rule has crossed >= N times in the last M minutes,
folds the cascade into ONE consolidated ``alert_type='FLAP'`` alert
instead of saving each per-crossing alert.

Covers (per workflow):
  - record_threshold_crossing persists one crossing
  - is_flapping False with 0 crossings
  - is_flapping False with < threshold
  - is_flapping True at >= threshold within window
  - is_flapping prunes old crossings (> window_minutes ago)
  - get_flap_window returns None when no crossings
  - get_flap_window returns FlapWindow with crossings/first/last
  - reset_flap_window clears the row
  - get_flapping_rules across multiple rules
  - get_flapping_rules per-user scoping
  - Per-user isolation
  - normalize_rule coerces the three new fields
  - normalize_rule defaults all three when missing
  - fire_rule with flap_detection_enabled=False behaves as today
  - fire_rule below threshold fires normally
  - fire_rule when flapping creates ONE FLAP alert
  - fire_rule when flapping bumps flap_suppressed counter
  - Subsequent fires within same window don't create more flap alerts
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from engine import alert_engine_v2 as engv2
from engine import flap_detector as flap
from engine.alert_engine_v2 import (
    ShippingAlert,
    _make,
    fire_rule,
    normalize_rule,
    save_alerts,
)


# ─── Fixture: isolate SQLite per test ──────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ───────────────────────────────────────────────────────────────

def _alert(
    *,
    alert_type: str = "BDI_MOVE",
    severity: str = "HIGH",
    ticker: str = "",
    value: float = 1000.0,
    change_pct: float = 5.0,
) -> ShippingAlert:
    return _make(
        alert_type, severity, "title", "body",
        ticker=ticker, value=value, threshold=5.0, change_pct=change_pct,
    )


def _flap_rule(
    rule_id: str = "rule_test",
    *,
    enabled: bool = True,
    window_minutes: int = 30,
    threshold_crossings: int = 5,
    severity: str = "HIGH",
    cooldown_minutes: int = 0,
) -> dict:
    """Build a minimal rule dict with flap-detection settings."""
    return {
        "rule_id": rule_id,
        "name": f"Test rule {rule_id}",
        "metric": "Freight Rate",
        "threshold": 5.0,
        "condition": "Above",
        "severity": severity,
        "enabled": True,
        "cooldown_minutes": cooldown_minutes,
        "flap_detection_enabled": enabled,
        "flap_window_minutes": window_minutes,
        "flap_threshold_crossings": threshold_crossings,
    }


def _read_crossings(rule_id: str, *, user_id: str = "") -> list:
    """Direct kv_state peek for assertions."""
    from state.db import get_connection
    row = get_connection().execute(
        "SELECT value FROM kv_state WHERE key = ?",
        (f"flap_window:{user_id}:{rule_id}",),
    ).fetchone()
    if row is None:
        return []
    blob = json.loads(row["value"])
    return blob.get("crossings", [])


def _count_rule_rows(rule_id: str) -> int:
    """Count alerts rows stamped with this rule_id."""
    from state.db import get_connection
    return int(
        get_connection().execute(
            "SELECT COUNT(*) FROM alerts WHERE rule_id = ?", (rule_id,)
        ).fetchone()[0]
    )


def _count_alert_type_rows(rule_id: str, alert_type: str) -> int:
    from state.db import get_connection
    return int(
        get_connection().execute(
            "SELECT COUNT(*) FROM alerts WHERE rule_id = ? AND alert_type = ?",
            (rule_id, alert_type),
        ).fetchone()[0]
    )


def _backdate_crossings(rule_id: str, *, minutes_ago: float, user_id: str = "") -> None:
    """Rewrite every crossing timestamp in the blob to ``minutes_ago``
    minutes in the past. Used by tests that want "old crossings"
    without sleeping the real wall clock."""
    from state.db import get_connection

    backdated = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat()
    key = f"flap_window:{user_id}:{rule_id}"
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM kv_state WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return
    blob = json.loads(row["value"])
    crossings = blob.get("crossings", [])
    blob["crossings"] = [backdated] * len(crossings)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (key, json.dumps(blob), datetime.now(timezone.utc).isoformat()),
        )


# ─── record_threshold_crossing ─────────────────────────────────────────────

def test_record_threshold_crossing_persists_one_crossing() -> None:
    """A single recorded crossing lands as one ISO timestamp in the
    kv_state blob under flap_window:<user_id>:<rule_id>."""
    flap.record_threshold_crossing("r1", "fire", user_id="")
    crossings = _read_crossings("r1", user_id="")
    assert len(crossings) == 1
    # Each entry is a parseable ISO-8601 timestamp.
    parsed = datetime.fromisoformat(crossings[0])
    assert parsed.tzinfo is not None  # tz-aware


# ─── is_flapping basic shape ───────────────────────────────────────────────

def test_is_flapping_false_with_zero_crossings() -> None:
    """No crossings recorded → not flapping."""
    assert flap.is_flapping("never_fired", user_id="") is False


def test_is_flapping_false_below_threshold() -> None:
    """Three crossings under a threshold of 5 → not flapping."""
    for _ in range(3):
        flap.record_threshold_crossing("r1", "fire", user_id="")
    assert flap.is_flapping(
        "r1", window_minutes=30, threshold_crossings=5, user_id=""
    ) is False


def test_is_flapping_true_at_threshold_within_window() -> None:
    """Five crossings within the 30-min window → flapping (>= threshold)."""
    for _ in range(5):
        flap.record_threshold_crossing("r1", "fire", user_id="")
    assert flap.is_flapping(
        "r1", window_minutes=30, threshold_crossings=5, user_id=""
    ) is True


# ─── Sliding-window pruning ────────────────────────────────────────────────

def test_is_flapping_prunes_old_crossings() -> None:
    """Crossings older than window_minutes ago do NOT count toward the
    flapping check. Six crossings recorded then backdated to 60 min
    ago should look like ZERO in-window crossings under a 30-min
    window."""
    for _ in range(6):
        flap.record_threshold_crossing("r1", "fire", user_id="")
    # Move every crossing to 60 minutes ago — outside the 30-min window.
    _backdate_crossings("r1", minutes_ago=60, user_id="")
    assert flap.is_flapping(
        "r1", window_minutes=30, threshold_crossings=5, user_id=""
    ) is False


# ─── get_flap_window ───────────────────────────────────────────────────────

def test_get_flap_window_none_when_no_crossings() -> None:
    """Without any recorded crossing, the snapshot is None."""
    assert flap.get_flap_window("never_recorded", user_id="") is None


def test_get_flap_window_returns_count_and_first_last() -> None:
    """The snapshot reports the pruned count plus the earliest and
    latest crossing timestamps still inside the window."""
    for _ in range(4):
        flap.record_threshold_crossing("r1", "fire", user_id="")
    win = flap.get_flap_window("r1", window_minutes=30, user_id="")
    assert win is not None
    assert win.rule_id == "r1"
    assert win.crossings == 4
    # ISO-8601 sort order matches chronological order.
    assert win.first_crossing_at <= win.last_crossing_at


# ─── reset_flap_window ─────────────────────────────────────────────────────

def test_reset_flap_window_clears_row() -> None:
    """After reset, the kv_state row is gone and is_flapping reads False."""
    for _ in range(5):
        flap.record_threshold_crossing("r1", "fire", user_id="")
    assert flap.is_flapping(
        "r1", window_minutes=30, threshold_crossings=5, user_id=""
    ) is True
    assert flap.reset_flap_window("r1", user_id="") is True
    assert _read_crossings("r1", user_id="") == []
    assert flap.is_flapping(
        "r1", window_minutes=30, threshold_crossings=5, user_id=""
    ) is False


# ─── get_flapping_rules ────────────────────────────────────────────────────

def test_get_flapping_rules_across_multiple_rules() -> None:
    """Two rules both crossing past threshold → both appear in the
    flapping list. A third rule below threshold does NOT appear."""
    for _ in range(5):
        flap.record_threshold_crossing("flap_a", "fire", user_id="")
        flap.record_threshold_crossing("flap_b", "fire", user_id="")
    # Third rule with only 2 crossings — below threshold.
    for _ in range(2):
        flap.record_threshold_crossing("calm_c", "fire", user_id="")

    flapping = flap.get_flapping_rules(
        window_minutes=30, threshold_crossings=5, user_id=""
    )
    rule_ids = sorted(w.rule_id for w in flapping)
    assert rule_ids == ["flap_a", "flap_b"]


def test_get_flapping_rules_per_user_scoping() -> None:
    """Passing user_id="alice" only returns alice's flapping rules.
    Bob's rules with the same rule_id do not appear in alice's list."""
    for _ in range(5):
        flap.record_threshold_crossing("shared", "fire", user_id="alice")
        flap.record_threshold_crossing("shared", "fire", user_id="bob")

    alice_view = flap.get_flapping_rules(
        window_minutes=30, threshold_crossings=5, user_id="alice"
    )
    bob_view = flap.get_flapping_rules(
        window_minutes=30, threshold_crossings=5, user_id="bob"
    )
    assert len(alice_view) == 1 and alice_view[0].rule_id == "shared"
    assert len(bob_view) == 1 and bob_view[0].rule_id == "shared"


# ─── Per-user isolation ────────────────────────────────────────────────────

def test_per_user_isolation_alice_crossings_do_not_count_for_bob() -> None:
    """Five crossings recorded under alice MUST NOT trip bob's flapping
    check on the same rule_id."""
    for _ in range(5):
        flap.record_threshold_crossing("shared", "fire", user_id="alice")
    assert flap.is_flapping(
        "shared", window_minutes=30, threshold_crossings=5, user_id="alice"
    ) is True
    assert flap.is_flapping(
        "shared", window_minutes=30, threshold_crossings=5, user_id="bob"
    ) is False


# ─── normalize_rule coercion ───────────────────────────────────────────────

def test_normalize_rule_coerces_all_three_flap_fields() -> None:
    """A blob carrying stringy / garbage values for the flap fields is
    coerced into clean types. Window clamps to >= 1, threshold to >= 2,
    enabled to a bool."""
    rule = {
        "rule_id": "r1",
        "flap_window_minutes": "45",      # numeric string
        "flap_threshold_crossings": "8",  # numeric string
        "flap_detection_enabled": 1,      # truthy non-bool
    }
    normalize_rule(rule)
    assert rule["flap_window_minutes"] == 45
    assert isinstance(rule["flap_window_minutes"], int)
    assert rule["flap_threshold_crossings"] == 8
    assert isinstance(rule["flap_threshold_crossings"], int)
    assert rule["flap_detection_enabled"] is True


def test_normalize_rule_defaults_all_three_when_missing() -> None:
    """A pre-v19 blob without any flap fields picks up the v19
    defaults: window=30, threshold=5, enabled=False (opt-in)."""
    rule = {"rule_id": "old_rule", "name": "old", "threshold": 5.0}
    normalize_rule(rule)
    assert rule["flap_window_minutes"] == 30
    assert rule["flap_threshold_crossings"] == 5
    assert rule["flap_detection_enabled"] is False


def test_normalize_rule_clamps_garbage_flap_values() -> None:
    """A garbage string for the numeric fields falls back to defaults;
    zero/negative window and threshold get clamped to safe minimums
    so a hand-edited blob cannot accidentally disable detection while
    leaving the enabled flag True."""
    rule = {
        "rule_id": "r1",
        "flap_window_minutes": "abc",
        "flap_threshold_crossings": "xyz",
        "flap_detection_enabled": "yes",
    }
    normalize_rule(rule)
    # Garbage strings → defaults (30, 5).
    assert rule["flap_window_minutes"] == 30
    assert rule["flap_threshold_crossings"] == 5
    # Truthy non-bool string → True.
    assert rule["flap_detection_enabled"] is True

    rule2 = {
        "rule_id": "r2",
        "flap_window_minutes": 0,
        "flap_threshold_crossings": 1,
        "flap_detection_enabled": False,
    }
    normalize_rule(rule2)
    # Window clamped to >= 1, threshold clamped to >= 2.
    assert rule2["flap_window_minutes"] == 1
    assert rule2["flap_threshold_crossings"] == 2
    assert rule2["flap_detection_enabled"] is False


# ─── fire_rule integration: legacy / off ───────────────────────────────────

def test_fire_rule_with_flap_disabled_behaves_as_today() -> None:
    """A rule with flap_detection_enabled=False NEVER calls into the
    flap detector. Five fires in a row each insert a row and no
    flap_window kv_state blob is written."""
    rule = _flap_rule(
        rule_id="r_off", enabled=False, threshold_crossings=2
    )
    for i in range(5):
        assert fire_rule(rule, [_alert(ticker=f"T{i}")], user_id="") is True
    assert _count_rule_rows("r_off") == 5
    # No flap blob persisted — detector was never consulted.
    assert _read_crossings("r_off", user_id="") == []
    # No FLAP-typed alert was emitted.
    assert _count_alert_type_rows("r_off", "FLAP") == 0


# ─── fire_rule integration: below threshold ────────────────────────────────

def test_fire_rule_flap_enabled_below_threshold_fires_normally() -> None:
    """With flap detection on but crossings below threshold, each fire
    still emits the underlying alert(s) and accumulates crossings."""
    rule = _flap_rule(
        rule_id="r_normal", enabled=True, threshold_crossings=5
    )
    # Three fires: each persists the underlying BDI_MOVE alert AND
    # records a crossing in the flap blob.
    # NOTE: Distinct tickers per fire so the v14 dedup_key does NOT
    # collapse them into a single row (the dedup key includes ticker).
    for i in range(3):
        assert fire_rule(
            rule, [_alert(ticker=f"T{i}")], user_id=""
        ) is True
    # Three crossings recorded.
    assert len(_read_crossings("r_normal", user_id="")) == 3
    # Three underlying alerts persisted (no FLAP yet — under threshold).
    assert _count_rule_rows("r_normal") == 3
    assert _count_alert_type_rows("r_normal", "FLAP") == 0


# ─── fire_rule integration: flapping → consolidated alert ──────────────────

def test_fire_rule_flapping_creates_one_consolidated_flap_alert() -> None:
    """Once the rule crosses the threshold, fire_rule emits ONE row
    of alert_type='FLAP' (instead of the underlying alert) and stamps
    the blob so further fires inside the window don't double-emit."""
    rule = _flap_rule(
        rule_id="r_flap", enabled=True, threshold_crossings=3
    )
    # Two fires under threshold — both persist as normal alerts.
    for i in range(2):
        fire_rule(rule, [_alert(ticker=f"T{i}")], user_id="")
    assert _count_alert_type_rows("r_flap", "FLAP") == 0
    # Third fire crosses the threshold → flap detected → ONE FLAP alert.
    fire_rule(rule, [_alert(ticker="T2")], user_id="")
    assert _count_alert_type_rows("r_flap", "FLAP") == 1


def test_flap_alert_severity_is_one_tier_below_rule_severity() -> None:
    """The consolidated FLAP alert de-escalates from the rule's
    severity by one tier (CRITICAL→HIGH, HIGH→MEDIUM)."""
    rule = _flap_rule(
        rule_id="r_sev", enabled=True, threshold_crossings=2,
        severity="HIGH",
    )
    fire_rule(rule, [_alert(ticker="A")], user_id="")
    fire_rule(rule, [_alert(ticker="B")], user_id="")

    from state.db import get_connection
    row = get_connection().execute(
        "SELECT severity FROM alerts WHERE rule_id = ? AND alert_type = 'FLAP'",
        ("r_sev",),
    ).fetchone()
    assert row is not None
    assert row["severity"] == "MEDIUM"


# ─── fire_rule integration: flap-suppressed counter ────────────────────────

def test_fire_rule_when_flapping_bumps_flap_suppressed_counter() -> None:
    """After the consolidated alert is emitted, subsequent fires in
    the same window bump the kv_state-backed flap_suppressed counter
    instead of persisting more rows."""
    rule = _flap_rule(
        rule_id="r_count", enabled=True, threshold_crossings=2
    )
    # First fire: under threshold (1 crossing), persists normally.
    fire_rule(rule, [_alert(ticker="A")], user_id="")
    # Second fire: trips threshold (2 crossings), emits FLAP alert.
    fire_rule(rule, [_alert(ticker="B")], user_id="")
    assert flap.get_flap_suppressed_count() == 0  # not yet bumped

    # Third + fourth fires: already flapping, already alerted →
    # silently swallowed and counted.
    fire_rule(rule, [_alert(ticker="C")], user_id="")
    fire_rule(rule, [_alert(ticker="D")], user_id="")
    assert flap.get_flap_suppressed_count() == 2


# ─── Once flapping, subsequent fires don't re-emit ─────────────────────────

def test_subsequent_fires_in_window_do_not_create_more_flap_alerts() -> None:
    """The consolidated alert is emitted at most ONCE per window. Five
    fires past the threshold should yield exactly one FLAP row."""
    rule = _flap_rule(
        rule_id="r_once", enabled=True, threshold_crossings=2
    )
    for i in range(7):
        fire_rule(rule, [_alert(ticker=f"T{i}")], user_id="")
    # Exactly one FLAP alert across the whole burst.
    assert _count_alert_type_rows("r_once", "FLAP") == 1
