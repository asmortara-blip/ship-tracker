"""Tests for engine.alert_engine_v2 rule persistence (save / load / reset).

Rules now live in the SQLite ``alert_rules`` table (see ``state.db``).
The legacy RULES_FILE constant is kept on the module for the one-time
JSON-to-SQLite migration helper. Each test runs against an isolated
SQLite DB at tmp_path so no test touches the real cache/ship_tracker.db.
"""
from __future__ import annotations

import pytest

from engine import alert_engine_v2 as engv2
from engine.alert_engine_v2 import load_rules, reset_rules, save_rules


# ─── Fixture: isolate persistence to a tmp file per test ────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db. Rules now live in SQLite
    instead of the legacy RULES_FILE."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Sample rule fixtures ───────────────────────────────────────────────────

def _sample_rules() -> list[dict]:
    return [
        {
            "id": "rule_test_1",
            "name": "Test Freight Spike",
            "metric": "Freight Rate",
            "threshold": 12.5,
            "condition": "Above",
            "severity": "Critical",
            "email_notify": True,
            "enabled": True,
        },
        {
            "id": "rule_test_2",
            "name": "Test Sentiment",
            "metric": "Sentiment Score",
            "threshold": -0.3,
            "condition": "Below",
            "severity": "Warning",
            "email_notify": False,
            "enabled": False,
        },
    ]


# ─── load_rules: edge cases ─────────────────────────────────────────────────

def test_load_rules_returns_empty_when_table_empty() -> None:
    """Fresh per-test DB: alert_rules table exists but has zero rows."""
    assert load_rules() == []


def test_load_rules_skips_corrupt_json_blob() -> None:
    """If the data column holds malformed JSON, that row is silently
    dropped from the loaded list (rather than raising)."""
    from state.db import get_connection

    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT INTO alert_rules (rule_id, data) VALUES (?, ?)",
            ("good", '{"id": "good", "name": "OK"}'),
        )
        conn.execute(
            "INSERT INTO alert_rules (rule_id, data) VALUES (?, ?)",
            ("bad", "{not valid json"),
        )
    loaded = load_rules()
    assert len(loaded) == 1
    assert loaded[0]["id"] == "good"


def test_load_rules_skips_non_dict_blob() -> None:
    """If the data column holds a JSON list or scalar (not a dict), the
    row is silently dropped."""
    from state.db import get_connection

    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT INTO alert_rules (rule_id, data) VALUES (?, ?)",
            ("good", '{"id": "good"}'),
        )
        conn.execute(
            "INSERT INTO alert_rules (rule_id, data) VALUES (?, ?)",
            ("non_dict", '["a list", "not a dict"]'),
        )
    loaded = load_rules()
    assert {r.get("id") for r in loaded} == {"good"}


# ─── save / load round-trip ─────────────────────────────────────────────────

def test_save_then_load_round_trips_rules_exactly() -> None:
    rules = _sample_rules()
    save_rules(rules)
    loaded = load_rules()
    assert loaded == rules


def test_save_creates_database_file(tmp_path, monkeypatch) -> None:
    """The SQLite DB file is lazily created on first save."""
    from state import db as state_db

    new_path = tmp_path / "lazy_init.db"
    monkeypatch.setattr(state_db, "DB_PATH", new_path)
    state_db.reset_for_tests()
    assert not new_path.exists()
    save_rules(_sample_rules())
    assert new_path.exists()


def test_save_empty_list_writes_empty_list() -> None:
    save_rules([])
    assert load_rules() == []


def test_save_overwrites_previous_file() -> None:
    save_rules(_sample_rules())
    assert len(load_rules()) == 2

    # Overwrite with a single-rule list.
    new = [{"id": "only_one", "name": "Solo", "threshold": 1.0,
            "condition": "Above", "severity": "Info", "enabled": True}]
    save_rules(new)
    loaded = load_rules()
    assert len(loaded) == 1
    assert loaded[0]["id"] == "only_one"


def test_save_accepts_generator_input() -> None:
    """save_rules calls list(rules) internally so a generator is OK."""
    save_rules(r for r in _sample_rules())
    assert load_rules() == _sample_rules()


# ─── reset_rules: behavior ──────────────────────────────────────────────────

def test_reset_rules_deletes_file() -> None:
    save_rules(_sample_rules())
    assert load_rules()  # truthy

    reset_rules()
    assert load_rules() == []


def test_reset_rules_when_table_empty_is_safe() -> None:
    """Reset against an already-empty table must not raise."""
    # Must not raise even before any save_rules call.
    reset_rules()
    assert load_rules() == []


# ─── Legacy path anchors ────────────────────────────────────────────────────

def test_legacy_rules_file_path_still_anchored_to_project_cache(monkeypatch) -> None:
    """The legacy RULES_FILE constant is kept on the module for the
    one-time JSON-to-SQLite migration helper. It must still point
    inside <project_root>/cache/alerts/ (siblings to ALERT_FILE)."""
    monkeypatch.undo()
    import importlib
    import engine.alert_engine_v2 as fresh
    importlib.reload(fresh)
    assert fresh.RULES_FILE.parent == fresh.ALERT_FILE.parent
    assert fresh.RULES_FILE.name == "rules.json"
