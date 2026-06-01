"""Tests for state.user_filters — per-user saved filter presets.

Storage is a single JSON blob per user under kv_state['user_filters:{user_id}'].
Presets are uniquely keyed by (scope, name). Per-user isolation guaranteed
by the user_id-scoped key.
"""
from __future__ import annotations

import pytest

from state.user_filters import (
    FilterPreset,
    delete_preset,
    get_preset,
    load_presets,
    save_preset,
)


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── FilterPreset shape ───────────────────────────────────────────────────

def test_filter_preset_shape() -> None:
    p = FilterPreset(name="critical-last-24h", scope="alerts",
                     payload={"severity": "CRITICAL", "window_hours": 24})
    assert p.name == "critical-last-24h"
    assert p.scope == "alerts"
    assert p.payload["severity"] == "CRITICAL"


# ─── save + load round-trip ───────────────────────────────────────────────

def test_save_and_load_round_trip() -> None:
    p = FilterPreset(name="zim-only", scope="alerts",
                     payload={"ticker": "ZIM"})
    assert save_preset(p, user_id="alice") is True
    loaded = load_presets(user_id="alice", scope="alerts")
    assert len(loaded) == 1
    assert loaded[0].name == "zim-only"
    assert loaded[0].payload == {"ticker": "ZIM"}


def test_load_presets_scope_filter() -> None:
    save_preset(FilterPreset("a", "alerts", {}), user_id="alice")
    save_preset(FilterPreset("b", "reports", {}), user_id="alice")
    alerts_only = load_presets(user_id="alice", scope="alerts")
    assert [p.name for p in alerts_only] == ["a"]


def test_load_presets_no_scope_returns_all_user_presets() -> None:
    save_preset(FilterPreset("a", "alerts", {}), user_id="alice")
    save_preset(FilterPreset("b", "reports", {}), user_id="alice")
    all_presets = load_presets(user_id="alice")
    assert {p.name for p in all_presets} == {"a", "b"}


def test_load_presets_unknown_user_returns_empty() -> None:
    assert load_presets(user_id="nobody") == []


# ─── get_preset ───────────────────────────────────────────────────────────

def test_get_preset_known_returns_preset() -> None:
    save_preset(FilterPreset("zim", "alerts", {"x": 1}), user_id="alice")
    p = get_preset("zim", "alerts", user_id="alice")
    assert p is not None
    assert p.payload == {"x": 1}


def test_get_preset_unknown_returns_none() -> None:
    assert get_preset("nope", "alerts", user_id="alice") is None


def test_get_preset_wrong_scope_returns_none() -> None:
    save_preset(FilterPreset("zim", "alerts", {}), user_id="alice")
    assert get_preset("zim", "reports", user_id="alice") is None


# ─── delete_preset ────────────────────────────────────────────────────────

def test_delete_preset_removes_only_specified() -> None:
    save_preset(FilterPreset("a", "alerts", {}), user_id="alice")
    save_preset(FilterPreset("b", "alerts", {}), user_id="alice")
    assert delete_preset("a", "alerts", user_id="alice") is True
    remaining = load_presets(user_id="alice", scope="alerts")
    assert [p.name for p in remaining] == ["b"]


def test_delete_preset_unknown_returns_false() -> None:
    assert delete_preset("nope", "alerts", user_id="alice") is False


# ─── Per-user isolation ───────────────────────────────────────────────────

def test_per_user_isolation() -> None:
    save_preset(FilterPreset("mine", "alerts", {"a": 1}), user_id="alice")
    save_preset(FilterPreset("yours", "alerts", {"b": 2}), user_id="bob")
    alice = load_presets(user_id="alice")
    bob = load_presets(user_id="bob")
    assert [p.name for p in alice] == ["mine"]
    assert [p.name for p in bob] == ["yours"]


# ─── Replace-on-save ──────────────────────────────────────────────────────

def test_save_replaces_existing_same_name_scope() -> None:
    save_preset(FilterPreset("zim", "alerts", {"v": 1}), user_id="alice")
    save_preset(FilterPreset("zim", "alerts", {"v": 2}), user_id="alice")
    presets = load_presets(user_id="alice", scope="alerts")
    assert len(presets) == 1
    assert presets[0].payload == {"v": 2}


# ─── Defensive ────────────────────────────────────────────────────────────

def test_save_preset_never_raises_on_bad_input() -> None:
    # Payload with a non-serializable object — save_preset coerces or
    # returns False, but does NOT raise into the caller.
    class _Unserializable:
        pass
    p = FilterPreset(name="x", scope="alerts",
                     payload={"obj": _Unserializable()})
    # Should not raise; result may be False
    result = save_preset(p, user_id="alice")
    assert result in (True, False)


def test_load_presets_with_corrupted_blob_returns_empty(monkeypatch) -> None:
    """If the kv_state blob is malformed, load_presets returns [] silently."""
    from state.db import get_connection
    from state.user_filters import _row_key

    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO kv_state (key, value, updated_at)
            VALUES (?, ?, '2026-05-22T00:00:00+00:00')
            """,
            (_row_key("alice"), "{not valid json"),
        )
    assert load_presets(user_id="alice") == []
