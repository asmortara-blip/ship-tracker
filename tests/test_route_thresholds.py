"""Tests for engine.route_thresholds — per-route alert threshold overrides.

Covers:
  - RouteThreshold dataclass shape (route_id, threshold_pct, severity;
    severity defaults to HIGH)
  - load_route_thresholds on an empty DB returns {}
  - load_route_thresholds + save_route_thresholds round-trip a small dict
  - load_route_thresholds tolerates malformed JSON (returns {})
  - load_route_thresholds tolerates a non-dict top-level value
  - load_route_thresholds drops malformed entries but keeps the rest
  - load_route_thresholds coerces an unknown severity back to HIGH
  - save_route_thresholds returns False on bad input (non-dict)
  - save_route_thresholds returns True on success
  - save_route_thresholds with an empty dict wipes the store
  - get_threshold_for_route returns the override when set
  - get_threshold_for_route returns the default when no override
  - get_threshold_for_route returns the function-level default arg
  - check_rate_alerts with NO overrides behaves identically to today
  - check_rate_alerts with override at 3% fires on a 4% move that would
    not have fired at 8%
  - check_rate_alerts with override at 15% does NOT fire on a 10% move
    that would have fired at 8%
  - check_rate_alerts severity override: when override.severity = CRITICAL,
    the resulting alert is CRITICAL regardless of magnitude
  - check_rate_alerts severity override: when override.severity = MEDIUM,
    the resulting alert is MEDIUM even on a 2× threshold move that
    would normally be CRITICAL
  - save_route_thresholds writes an audit event with the correct count
  - save_route_thresholds audit count reflects the input dict size,
    not the filtered-and-encoded size
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from engine.route_thresholds import (
    RouteThreshold,
    get_threshold_for_route,
    load_route_thresholds,
    save_route_thresholds,
)
from engine.alert_engine_v2 import check_rate_alerts


# ─── Fixture: isolate the SQLite DB per test ───────────────────────────────

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

def _rate_df(values: list[float]) -> pd.DataFrame:
    """Build a freight-rate DataFrame with 'date' + 'rate_usd_per_feu'."""
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(values), freq="D"),
        "rate_usd_per_feu": values,
    })


def _write_raw_kv(value: str) -> None:
    """Bypass save_route_thresholds to write a raw 'value' under the
    route_thresholds kv_state key. Used to test malformed-JSON tolerance."""
    from datetime import datetime, timezone

    from state.db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO kv_state (key, value, updated_at) VALUES (?, ?, ?)",
        ("route_thresholds", value, datetime.now(timezone.utc).isoformat()),
    )


# ─── Dataclass shape ───────────────────────────────────────────────────────

def test_route_threshold_dataclass_shape() -> None:
    rt = RouteThreshold(route_id="r1", threshold_pct=5.0, severity="CRITICAL")
    assert rt.route_id == "r1"
    assert rt.threshold_pct == 5.0
    assert rt.severity == "CRITICAL"


def test_route_threshold_default_severity_is_high() -> None:
    """Per the spec: severity defaults to HIGH when not specified."""
    rt = RouteThreshold(route_id="r1", threshold_pct=5.0)
    assert rt.severity == "HIGH"


# ─── load_route_thresholds: empty + round-trip ─────────────────────────────

def test_load_on_empty_db_returns_empty_dict() -> None:
    """Fresh DB → no kv_state row → load returns {}."""
    assert load_route_thresholds() == {}


def test_save_then_load_round_trips_a_small_dict() -> None:
    """A two-entry save survives the round trip with values intact."""
    payload = {
        "transpacific_eb": RouteThreshold(
            route_id="transpacific_eb", threshold_pct=3.0, severity="HIGH",
        ),
        "transatlantic": RouteThreshold(
            route_id="transatlantic", threshold_pct=15.0, severity="CRITICAL",
        ),
    }
    assert save_route_thresholds(payload) is True
    loaded = load_route_thresholds()
    assert set(loaded.keys()) == {"transpacific_eb", "transatlantic"}
    assert loaded["transpacific_eb"].threshold_pct == 3.0
    assert loaded["transpacific_eb"].severity == "HIGH"
    assert loaded["transatlantic"].threshold_pct == 15.0
    assert loaded["transatlantic"].severity == "CRITICAL"


# ─── load_route_thresholds: malformed-input tolerance ──────────────────────

def test_load_tolerates_malformed_json() -> None:
    """A garbage value in the kv_state row → load returns {}, never raises."""
    _write_raw_kv("{not-valid-json")
    assert load_route_thresholds() == {}


def test_load_tolerates_non_dict_top_level() -> None:
    """A JSON list at the top level (not a dict) → load returns {}."""
    _write_raw_kv(json.dumps([{"threshold_pct": 5.0}]))
    assert load_route_thresholds() == {}


def test_load_drops_malformed_entry_keeps_rest() -> None:
    """An entry without ``threshold_pct`` is dropped; valid siblings survive."""
    _write_raw_kv(json.dumps({
        "good": {"threshold_pct": 5.0, "severity": "HIGH"},
        "no_threshold": {"severity": "HIGH"},          # missing field
        "bad_threshold": {"threshold_pct": "abc"},      # not numeric
    }))
    loaded = load_route_thresholds()
    assert set(loaded.keys()) == {"good"}
    assert loaded["good"].threshold_pct == 5.0


def test_load_coerces_unknown_severity_back_to_default() -> None:
    """A bogus severity string falls back to HIGH (the default)."""
    _write_raw_kv(json.dumps({
        "r1": {"threshold_pct": 5.0, "severity": "OMG_PANIC"},
    }))
    loaded = load_route_thresholds()
    assert loaded["r1"].severity == "HIGH"


# ─── save_route_thresholds: error paths + edge cases ───────────────────────

def test_save_returns_false_on_non_dict_input() -> None:
    """Pass a list instead of a dict → save returns False, no crash."""
    assert save_route_thresholds(["not", "a", "dict"]) is False  # type: ignore[arg-type]


def test_save_with_empty_dict_writes_empty_blob() -> None:
    """An empty save persists an empty JSON object so a later load
    returns {} (effectively wiping any previous overrides)."""
    # First seed with one entry, then wipe with an empty save.
    save_route_thresholds({
        "r1": RouteThreshold("r1", 5.0, "HIGH"),
    })
    assert load_route_thresholds() != {}
    assert save_route_thresholds({}) is True
    assert load_route_thresholds() == {}


# ─── get_threshold_for_route ───────────────────────────────────────────────

def test_get_threshold_returns_override_when_set() -> None:
    save_route_thresholds({
        "transpacific_eb": RouteThreshold("transpacific_eb", 3.0, "HIGH"),
    })
    assert get_threshold_for_route("transpacific_eb") == 3.0


def test_get_threshold_returns_default_when_no_override() -> None:
    """No override saved → returns the parameter default (8.0)."""
    assert get_threshold_for_route("unknown_route") == 8.0


def test_get_threshold_honors_custom_default() -> None:
    """Caller-supplied default propagates when no override is set."""
    assert get_threshold_for_route("unknown_route", default=12.5) == 12.5


# ─── check_rate_alerts backward compatibility ──────────────────────────────

def test_check_rate_alerts_no_overrides_behaves_identically() -> None:
    """With no overrides saved, the function still fires at 8% on a
    +15% move (the legacy behaviour). A 5% move still does not fire."""
    # No overrides — fresh DB.
    fires = check_rate_alerts({"r": _rate_df([1000.0] * 7 + [1150.0])})
    assert len(fires) == 1
    assert fires[0].alert_type == "RATE_SURGE"
    # Auto-detected severity stays HIGH at +15% (< 2×8% = 16%).
    assert fires[0].severity == "HIGH"

    no_fire = check_rate_alerts({"r": _rate_df([1000.0] * 7 + [1050.0])})
    assert no_fire == []


# ─── check_rate_alerts: tighter override fires what default would skip ─────

def test_override_at_3pct_fires_on_4pct_move() -> None:
    """transpacific_eb override at 3% → +4% move fires (default 8% would not)."""
    save_route_thresholds({
        "transpacific_eb": RouteThreshold(
            route_id="transpacific_eb", threshold_pct=3.0, severity="HIGH",
        ),
    })
    fires = check_rate_alerts({
        "transpacific_eb": _rate_df([1000.0] * 7 + [1040.0]),
    })
    assert len(fires) == 1
    assert fires[0].route_id == "transpacific_eb"
    assert fires[0].alert_type == "RATE_SURGE"
    # The effective threshold the alert carries should be the override.
    assert fires[0].threshold == 3.0


def test_override_at_3pct_does_not_double_fire_with_default() -> None:
    """Sanity: a +4% move on a route WITHOUT an override still respects
    the function-level 8% default and does not fire."""
    save_route_thresholds({
        "transpacific_eb": RouteThreshold("transpacific_eb", 3.0, "HIGH"),
    })
    # Different route_id — no override applies.
    fires = check_rate_alerts({
        "transatlantic": _rate_df([1000.0] * 7 + [1040.0]),
    })
    assert fires == []


# ─── check_rate_alerts: looser override suppresses what default would fire ──

def test_override_at_15pct_does_not_fire_on_10pct_move() -> None:
    """transpacific_eb override at 15% → +10% move does NOT fire
    (default 8% threshold would have fired)."""
    save_route_thresholds({
        "transpacific_eb": RouteThreshold(
            route_id="transpacific_eb", threshold_pct=15.0, severity="HIGH",
        ),
    })
    fires = check_rate_alerts({
        "transpacific_eb": _rate_df([1000.0] * 7 + [1100.0]),
    })
    assert fires == []


# ─── check_rate_alerts: severity override pins the tier ────────────────────

def test_severity_override_critical_applies_at_any_magnitude() -> None:
    """When the override's severity = CRITICAL, the resulting alert is
    CRITICAL regardless of move magnitude — even a small +5% move on a
    3%-threshold override produces a CRITICAL alert."""
    save_route_thresholds({
        "transpacific_eb": RouteThreshold(
            route_id="transpacific_eb", threshold_pct=3.0, severity="CRITICAL",
        ),
    })
    fires = check_rate_alerts({
        "transpacific_eb": _rate_df([1000.0] * 7 + [1050.0]),  # +5% move
    })
    assert len(fires) == 1
    # +5% over a 3% threshold is < 2× → would normally be HIGH. Override
    # pins it to CRITICAL.
    assert fires[0].severity == "CRITICAL"


def test_severity_override_medium_pins_down_a_critical_move() -> None:
    """Inverse direction: override.severity = MEDIUM on a 2× threshold
    move that would normally auto-detect as CRITICAL. The override wins."""
    save_route_thresholds({
        "transpacific_eb": RouteThreshold(
            route_id="transpacific_eb", threshold_pct=5.0, severity="MEDIUM",
        ),
    })
    fires = check_rate_alerts({
        "transpacific_eb": _rate_df([1000.0] * 7 + [1200.0]),  # +20% move
    })
    assert len(fires) == 1
    # +20% is 4× the 5% threshold — auto-detect would say CRITICAL. The
    # MEDIUM override demonstrates the severity pin works both ways.
    assert fires[0].severity == "MEDIUM"


# ─── Audit hook ────────────────────────────────────────────────────────────

def test_save_writes_audit_event_with_correct_count() -> None:
    """save_route_thresholds calls record_audit('save_route_thresholds',
    detail={'count': N}) where N is the input dict length."""
    save_route_thresholds({
        "r1": RouteThreshold("r1", 5.0, "HIGH"),
        "r2": RouteThreshold("r2", 7.0, "CRITICAL"),
        "r3": RouteThreshold("r3", 3.0, "MEDIUM"),
    })
    from state.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT action, detail_json FROM audit_events "
        "WHERE action = 'save_route_thresholds'"
    ).fetchall()
    assert len(rows) == 1
    detail = json.loads(rows[0]["detail_json"])
    assert detail == {"count": 3}


def test_save_audit_count_reflects_input_size_not_filtered() -> None:
    """The audit detail['count'] records the input dict length, not the
    post-filter size — security review wants 'user saved N overrides'."""
    # One valid entry + one bogus (non-RouteThreshold) entry. The encoder
    # drops the bogus one, but the audit count should still report 2.
    save_route_thresholds({
        "r1": RouteThreshold("r1", 5.0, "HIGH"),
        "r2": "not-a-RouteThreshold",  # type: ignore[dict-item]
    })
    from state.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT detail_json FROM audit_events "
        "WHERE action = 'save_route_thresholds'"
    ).fetchall()
    assert len(rows) == 1
    detail = json.loads(rows[0]["detail_json"])
    assert detail["count"] == 2
