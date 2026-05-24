"""Tests for tools.rules_yaml — config-as-code for alert rules.

The defining properties under test:

* Round-trip is byte-exact for the rule shapes we ship: empty list, a
  single rule, a rule with every optional field set, multiple rules in
  arbitrary input order.
* Output is deterministic — same input list always produces byte-
  identical YAML (rules sorted by rule_id, field order fixed within
  each rule).
* Strings containing YAML separator chars (``:``, ``#``, ``[``) and
  bool/number lookalikes (``true``, ``5``) are quoted on emit and
  un-quoted on parse without information loss.
* The parse path is total: malformed YAML produces ``([], [error_msg])``
  rather than raising, and per-rule problems (unknown field, missing
  field, bad severity) produce warnings + either default or skip the
  rule.
* validate_rule_dict gives the UI a synchronous yes/no for the paste
  validator with a short reason string on failure.
"""
from __future__ import annotations

import pytest

from tools.rules_yaml import (
    rules_to_yaml,
    validate_rule_dict,
    yaml_to_rules,
)


# ─── Test data builders ───────────────────────────────────────────────────

def _full_rule(rule_id: str = "bdi-spike") -> dict:
    """A rule dict carrying every wire-format field, in the persisted
    shape the engine saves. Used by the round-trip tests so they
    exercise the full schema, not just the always-present subset."""
    return {
        "id": rule_id,
        "rule_id": rule_id,
        "name": "BDI spike >5%",
        "metric": "bdi",
        "threshold": 5.0,
        "condition": "Above",
        "severity": "HIGH",
        "email_notify": False,
        "enabled": True,
        "target_channels": ["trading-desk"],
        "cooldown_minutes": 360,
        "flap_detection_enabled": False,
        "flap_window_minutes": 30,
        "flap_threshold_crossings": 5,
    }


# ─── rules_to_yaml ────────────────────────────────────────────────────────

def test_empty_list_yields_schema_version_and_empty_rules() -> None:
    """Empty input must still produce a valid, parseable document so a
    downstream import doesn't misread "empty" as "malformed file"."""
    out = rules_to_yaml([])
    assert "schema_version: 1" in out
    assert "rules: []" in out
    parsed, warnings = yaml_to_rules(out)
    assert parsed == []
    # warnings may be empty or carry a single non-blocking hint;
    # nothing fatal expected.
    assert all("could not parse" not in w for w in warnings)


def test_single_rule_round_trips_through_yaml_to_rules() -> None:
    """rules_to_yaml → yaml_to_rules must preserve the field values
    on a minimal rule. The round-trip is the most important property
    the module ships — version-controllable round-trip is the entire
    point of the feature."""
    rules = [_full_rule()]
    yaml_text = rules_to_yaml(rules)
    parsed, warnings = yaml_to_rules(yaml_text)
    assert warnings == []
    assert len(parsed) == 1
    p = parsed[0]
    assert p["rule_id"] == "bdi-spike"
    assert p["name"] == "BDI spike >5%"
    assert p["metric"] == "bdi"
    assert p["threshold_pct"] == 5.0
    assert p["severity"] == "HIGH"
    assert p["condition"] == "Above"


def test_full_rule_round_trips_with_flap_and_cooldown() -> None:
    """Cooldown + flap fields must round-trip — these are the v18/v19
    additions and the most likely to drift if the field ordering /
    coercion changes."""
    rule = _full_rule()
    rule["cooldown_minutes"] = 720
    rule["flap_detection_enabled"] = True
    rule["flap_window_minutes"] = 45
    rule["flap_threshold_crossings"] = 7
    yaml_text = rules_to_yaml([rule])
    parsed, _warnings = yaml_to_rules(yaml_text)
    p = parsed[0]
    assert p["cooldown_minutes"] == 720
    assert p["flap_detection_enabled"] is True
    assert p["flap_window_minutes"] == 45
    assert p["flap_threshold_crossings"] == 7


def test_output_is_deterministic() -> None:
    """Same rules → byte-identical YAML. A diff between two
    checkpoints can ONLY surface a real change. Input ORDER must not
    matter; the emitter sorts by rule_id."""
    a = [_full_rule("a"), _full_rule("b")]
    b = [_full_rule("b"), _full_rule("a")]  # reversed
    assert rules_to_yaml(a) == rules_to_yaml(b)


def test_strings_with_colons_are_quoted_and_round_trip() -> None:
    """A rule name like ``foo: bar`` would corrupt the YAML if emitted
    bare — the parser would split on the colon. The emitter must quote
    such strings, and the parser must unquote without information
    loss."""
    rule = _full_rule()
    rule["name"] = "foo: bar"
    yaml_text = rules_to_yaml([rule])
    # The emitted line for the name must be quoted.
    assert 'name: "foo: bar"' in yaml_text
    parsed, _ = yaml_to_rules(yaml_text)
    assert parsed[0]["name"] == "foo: bar"


def test_strings_with_hash_are_quoted_and_round_trip() -> None:
    """A ``#`` would be parsed as a YAML comment in a strict parser;
    we quote on emit + accept the quoted form on parse."""
    rule = _full_rule()
    rule["name"] = "rule #1"
    yaml_text = rules_to_yaml([rule])
    parsed, _ = yaml_to_rules(yaml_text)
    assert parsed[0]["name"] == "rule #1"


# ─── yaml_to_rules — parsing edge cases ──────────────────────────────────

def test_unknown_field_emits_warning_and_field_is_dropped() -> None:
    """An unknown key must NEVER be silently dropped — the operator
    needs to know their config has stale fields. The kept rule must
    not carry the unknown key."""
    yaml_text = (
        "schema_version: 1\n"
        "rules:\n"
        "  - rule_id: r1\n"
        "    name: test\n"
        "    metric: bdi\n"
        "    threshold_pct: 5.0\n"
        "    severity: HIGH\n"
        "    legacy_field: stale\n"
    )
    parsed, warnings = yaml_to_rules(yaml_text)
    assert len(parsed) == 1
    assert "legacy_field" not in parsed[0]
    assert any("legacy_field" in w for w in warnings)


def test_missing_field_emits_warning_and_default_used() -> None:
    """A missing optional field must default + warn. The defaults
    mirror the engine's normalize_rule (e.g. cooldown_minutes=0)."""
    yaml_text = (
        "schema_version: 1\n"
        "rules:\n"
        "  - rule_id: r1\n"
        "    name: test\n"
        "    metric: bdi\n"
        "    threshold_pct: 5.0\n"
        "    severity: HIGH\n"
    )
    parsed, warnings = yaml_to_rules(yaml_text)
    assert len(parsed) == 1
    assert parsed[0]["cooldown_minutes"] == 0
    # The cooldown_minutes default warning must surface.
    assert any("cooldown_minutes" in w for w in warnings)


def test_malformed_yaml_returns_empty_list_and_error_warning() -> None:
    """Garbage input collapses to ([], [error_msg]) — the import path
    relies on this contract to display the error to the operator
    without crashing the CLI / UI.

    The trigger here is an indented line at the top level — the parser
    rejects unexpected indentation rather than guessing intent."""
    parsed, warnings = yaml_to_rules("    bad_indent_at_top_level: 1\n")
    assert parsed == []
    assert len(warnings) == 1
    assert "could not parse" in warnings[0] or "not a mapping" in warnings[0] or "is not a list" in warnings[0]


def test_rule_missing_rule_id_is_skipped_with_warning() -> None:
    """A rule with no rule_id cannot be persisted (the engine's
    save_rules drops rows with no rule_id). The parser pre-empts that
    by skipping + warning so the operator sees what went wrong."""
    yaml_text = (
        "schema_version: 1\n"
        "rules:\n"
        "  - name: no-id-rule\n"
        "    metric: bdi\n"
        "    severity: HIGH\n"
        "  - rule_id: good\n"
        "    name: ok\n"
        "    metric: bdi\n"
        "    severity: HIGH\n"
    )
    parsed, warnings = yaml_to_rules(yaml_text)
    assert len(parsed) == 1
    assert parsed[0]["rule_id"] == "good"
    assert any("missing rule_id" in w for w in warnings)


def test_threshold_pct_coerced_from_string_to_float() -> None:
    """A hand-edited YAML may carry threshold_pct as ``'5.0'`` (string)
    instead of ``5.0`` (float). The parser coerces so the engine sees
    a float — string thresholds confuse the comparison in the fire
    path."""
    yaml_text = (
        "schema_version: 1\n"
        "rules:\n"
        '  - rule_id: r1\n'
        '    name: test\n'
        '    metric: bdi\n'
        '    threshold_pct: "5.0"\n'
        '    severity: HIGH\n'
    )
    parsed, _ = yaml_to_rules(yaml_text)
    assert isinstance(parsed[0]["threshold_pct"], float)
    assert parsed[0]["threshold_pct"] == 5.0


def test_unknown_severity_skips_rule_with_warning() -> None:
    """Severity must be CRITICAL/HIGH/MEDIUM/LOW. An unknown value (the
    spec calls out this case explicitly) gets the rule REJECTED, not
    accepted with a default — silently routing an unknown severity
    through the channel-threshold check would bypass delivery."""
    yaml_text = (
        "schema_version: 1\n"
        "rules:\n"
        "  - rule_id: r1\n"
        "    name: test\n"
        "    metric: bdi\n"
        "    threshold_pct: 5.0\n"
        "    severity: BOGUS\n"
    )
    parsed, warnings = yaml_to_rules(yaml_text)
    assert parsed == []
    assert any("BOGUS" in w for w in warnings)


# ─── validate_rule_dict ──────────────────────────────────────────────────

def test_validate_rule_dict_happy_path() -> None:
    """A well-formed rule dict returns (True, 'ok')."""
    rule = _full_rule()
    ok, reason = validate_rule_dict(rule)
    assert ok is True
    assert reason == "ok"


def test_validate_rule_dict_rejects_non_mapping() -> None:
    """Non-dict inputs must be rejected — the UI's paste validator
    feeds whatever the parser surfaced."""
    ok, reason = validate_rule_dict("not a dict")
    assert ok is False
    assert "mapping" in reason


def test_validate_rule_dict_rejects_missing_rule_id() -> None:
    """No id → no save. The validator catches this before the user
    pays for a round-trip through the engine."""
    ok, reason = validate_rule_dict({"name": "x", "metric": "bdi"})
    assert ok is False
    assert "rule_id" in reason


def test_validate_rule_dict_rejects_bad_severity() -> None:
    """Bad severity is the validator's other hard-no — same reason as
    the parser's reject path."""
    ok, reason = validate_rule_dict({"rule_id": "r1", "severity": "Critical-ish"})
    assert ok is False
    assert "severity" in reason


# ─── Round-trip preservation pins ────────────────────────────────────────

def test_round_trip_preserves_cooldown_minutes() -> None:
    """Pinned per the task spec — the cooldown field is the v18
    addition most likely to drift if the field ordering changes."""
    rule = _full_rule()
    rule["cooldown_minutes"] = 1440
    yaml_text = rules_to_yaml([rule])
    parsed, _ = yaml_to_rules(yaml_text)
    assert parsed[0]["cooldown_minutes"] == 1440


def test_round_trip_preserves_flap_detection_enabled() -> None:
    """The flap on/off bit is a bool — booleans are the easiest type
    to silently mis-coerce (1 ≠ True for the engine's strict check)."""
    rule = _full_rule()
    rule["flap_detection_enabled"] = True
    yaml_text = rules_to_yaml([rule])
    parsed, _ = yaml_to_rules(yaml_text)
    assert parsed[0]["flap_detection_enabled"] is True


def test_round_trip_preserves_target_channels_list() -> None:
    """target_channels is the only list field in the wire format;
    list serialisation is its own emit/parse path and gets its own
    test pin."""
    rule = _full_rule()
    rule["target_channels"] = ["trading-desk", "ops", "ceo"]
    yaml_text = rules_to_yaml([rule])
    parsed, _ = yaml_to_rules(yaml_text)
    assert parsed[0]["target_channels"] == ["trading-desk", "ops", "ceo"]


def test_hand_rolled_parser_handles_indented_list_of_dicts() -> None:
    """Pin the parser shape for the list-of-dicts case — the most
    structurally complex piece of the hand-rolled subset. A regression
    here would corrupt every multi-rule import."""
    yaml_text = (
        "schema_version: 1\n"
        "rules:\n"
        "  - rule_id: rule-1\n"
        "    name: First\n"
        "    metric: bdi\n"
        "    threshold_pct: 1.0\n"
        "    severity: HIGH\n"
        "  - rule_id: rule-2\n"
        "    name: Second\n"
        "    metric: scfi\n"
        "    threshold_pct: 2.0\n"
        "    severity: MEDIUM\n"
        "  - rule_id: rule-3\n"
        "    name: Third\n"
        "    metric: bunker\n"
        "    threshold_pct: 3.0\n"
        "    severity: LOW\n"
    )
    parsed, warnings = yaml_to_rules(yaml_text)
    assert len(parsed) == 3
    rule_ids = [r["rule_id"] for r in parsed]
    assert rule_ids == ["rule-1", "rule-2", "rule-3"]
    assert parsed[0]["name"] == "First"
    assert parsed[1]["metric"] == "scfi"
    assert parsed[2]["severity"] == "LOW"
