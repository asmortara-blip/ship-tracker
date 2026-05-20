"""Tests for engine.alert_engine_v2 rule persistence (save / load / reset).

Each test runs against an isolated, monkeypatched RULES_FILE path so the
suite doesn't touch the real cache/alerts/rules.json. Round-trips are
verified by value-equality, and edge cases (missing file, malformed JSON,
empty list) are pinned to "returns []" for graceful degradation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine import alert_engine_v2 as engv2
from engine.alert_engine_v2 import load_rules, reset_rules, save_rules


# ─── Fixture: isolate persistence to a tmp file per test ────────────────────

@pytest.fixture(autouse=True)
def isolated_rules_file(monkeypatch, tmp_path):
    """Redirect the module-level RULES_FILE at every test so persistence
    writes go to a per-test tmp dir, never the real cache."""
    tmp_file = tmp_path / "rules.json"
    monkeypatch.setattr(engv2, "RULES_FILE", tmp_file)
    yield tmp_file


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

def test_load_rules_returns_empty_when_file_missing(isolated_rules_file: Path) -> None:
    assert not isolated_rules_file.exists()
    assert load_rules() == []


def test_load_rules_returns_empty_on_corrupt_json(isolated_rules_file: Path) -> None:
    isolated_rules_file.parent.mkdir(parents=True, exist_ok=True)
    isolated_rules_file.write_text("{ this is not json", encoding="utf-8")
    assert load_rules() == []


def test_load_rules_returns_empty_on_non_list_payload(isolated_rules_file: Path) -> None:
    isolated_rules_file.parent.mkdir(parents=True, exist_ok=True)
    isolated_rules_file.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    assert load_rules() == []


# ─── save / load round-trip ─────────────────────────────────────────────────

def test_save_then_load_round_trips_rules_exactly() -> None:
    rules = _sample_rules()
    save_rules(rules)
    loaded = load_rules()
    assert loaded == rules


def test_save_creates_parent_directories(isolated_rules_file: Path) -> None:
    # Parent doesn't exist yet under tmp_path/rules.json — save should mkdir.
    assert not isolated_rules_file.parent.exists() or isolated_rules_file.parent.iterdir
    save_rules(_sample_rules())
    assert isolated_rules_file.exists()
    assert isolated_rules_file.parent.exists()


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


def test_reset_rules_when_file_missing_is_safe(isolated_rules_file: Path) -> None:
    assert not isolated_rules_file.exists()
    # Must not raise.
    reset_rules()
    assert load_rules() == []


# ─── File location: project-root anchor ─────────────────────────────────────

def test_default_rules_file_lives_in_project_cache(monkeypatch) -> None:
    """The non-test default lives inside <project_root>/cache/alerts/.

    This is the same anchoring used for ALERT_FILE (commit c409bb0 fixed
    cache paths to be CWD-independent). Re-import the module fresh to pick
    up the un-monkeypatched constant.
    """
    monkeypatch.undo()
    import importlib
    import engine.alert_engine_v2 as fresh
    importlib.reload(fresh)
    # Should be under <project_root>/cache/alerts/rules.json — siblings to
    # ALERT_FILE.
    assert fresh.RULES_FILE.parent == fresh.ALERT_FILE.parent
    assert fresh.RULES_FILE.name == "rules.json"
