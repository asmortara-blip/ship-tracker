"""Tests for tools.rules_csv — CSV round-trip for alert rules.

Mirrors :mod:`tests.test_rules_yaml` but for the CSV wire format. The
defining properties under test:

* Round-trip is value-exact (every wire field preserved) on the rule
  shapes we ship: empty list, single rule, full rule, multiple rules.
* Output is deterministic — same input always produces byte-identical
  CSV (rules sorted by ``rule_id``, fixed column order, fixed BOM
  prefix).
* The CSV delimiter (``,``) cannot collide with ``target_channels``
  because the list joins on ``|``. The pipe-split is symmetric.
* The parse path is total: per-row problems (unknown column, missing
  rule_id, invalid severity, bad threshold_pct) produce warnings +
  either default or skip the row. Top-level parse errors collapse to
  ``([], [error_msg])``.
* ``validate_rule_csv_row`` gives the UI's paste validator a sync
  yes/no with a short reason string on failure.
"""
from __future__ import annotations

from tools.rules_csv import (
    COLUMNS,
    csv_to_rules,
    rules_to_csv,
    validate_rule_csv_row,
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
        "threshold_pct": 5.0,
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


# UTF-8 BOM — spelled out literally for grep / portability. Matches the
# constant inside the module.
_BOM = "﻿"


# ─── rules_to_csv ─────────────────────────────────────────────────────────

def test_empty_list_yields_header_row_only() -> None:
    """Empty input must still produce a valid CSV — BOM + the header
    row — so a downstream parser can tell "no rules" apart from
    "malformed file". The first non-BOM line is the header."""
    out = rules_to_csv([])
    assert out.startswith(_BOM)
    body = out[len(_BOM):]
    lines = [ln for ln in body.splitlines() if ln]
    assert len(lines) == 1
    # Header carries every column in the canonical order.
    expected = ",".join(COLUMNS)
    assert lines[0] == expected


def test_single_rule_round_trips_through_csv_to_rules() -> None:
    """rules_to_csv → csv_to_rules must preserve field values on a
    minimal rule. This is the most important property the module
    ships — version-controllable round-trip is the entire point."""
    rules = [_full_rule()]
    csv_text = rules_to_csv(rules)
    parsed, warnings = csv_to_rules(csv_text)
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
    additions and the most likely to drift if the column ordering or
    coercion changes."""
    rule = _full_rule()
    rule["cooldown_minutes"] = 720
    rule["flap_detection_enabled"] = True
    rule["flap_window_minutes"] = 45
    rule["flap_threshold_crossings"] = 7
    csv_text = rules_to_csv([rule])
    parsed, _ = csv_to_rules(csv_text)
    p = parsed[0]
    assert p["cooldown_minutes"] == 720
    assert p["flap_detection_enabled"] is True
    assert p["flap_window_minutes"] == 45
    assert p["flap_threshold_crossings"] == 7


def test_target_channels_joined_with_pipe_not_comma() -> None:
    """target_channels MUST join with ``|`` — comma would collide with
    the CSV delimiter and tear the cell into multiple columns. The
    test pins both the emit + parse path."""
    rule = _full_rule()
    rule["target_channels"] = ["trading-desk", "ops", "ceo"]
    csv_text = rules_to_csv([rule])
    # The pipe-delimited string must appear verbatim somewhere in the
    # output — not split into separate columns.
    assert "trading-desk|ops|ceo" in csv_text
    parsed, _ = csv_to_rules(csv_text)
    assert parsed[0]["target_channels"] == ["trading-desk", "ops", "ceo"]


def test_output_is_deterministic() -> None:
    """Same rules → byte-identical CSV. A diff between two checkpoints
    can ONLY surface a real change. Input ORDER must not matter; the
    emitter sorts by rule_id."""
    a = [_full_rule("a"), _full_rule("b")]
    b = [_full_rule("b"), _full_rule("a")]  # reversed
    assert rules_to_csv(a) == rules_to_csv(b)


def test_commas_in_name_field_are_quoted() -> None:
    """A rule name with a comma would tear the row if emitted bare —
    csv.QUOTE_MINIMAL wraps such cells in double quotes. The
    round-trip must preserve the comma without splitting."""
    rule = _full_rule()
    rule["name"] = "Spike, big one"
    csv_text = rules_to_csv([rule])
    # The csv module quotes the cell.
    assert '"Spike, big one"' in csv_text
    parsed, _ = csv_to_rules(csv_text)
    assert parsed[0]["name"] == "Spike, big one"


def test_output_has_bom_prefix_for_excel() -> None:
    """Excel on macOS / Windows otherwise mis-detects UTF-8 as
    Windows-1252 and a non-ASCII rule name displays as mojibake. The
    BOM forces UTF-8 detection."""
    out = rules_to_csv([_full_rule()])
    assert out.startswith(_BOM)
    # The BOM is exactly three bytes — \xef\xbb\xbf.
    assert out.encode("utf-8").startswith(b"\xef\xbb\xbf")


# ─── csv_to_rules — parse edge cases ─────────────────────────────────────

def test_csv_to_rules_strips_bom() -> None:
    """A BOM-prefixed CSV must parse the same as one without — the
    DictReader would otherwise treat the BOM as part of the first
    column name and the rule_id lookup would silently miss every row."""
    rule = _full_rule()
    csv_text = rules_to_csv([rule])  # already has BOM
    parsed_with_bom, _ = csv_to_rules(csv_text)
    parsed_without_bom, _ = csv_to_rules(csv_text[len(_BOM):])
    assert parsed_with_bom == parsed_without_bom
    assert len(parsed_with_bom) == 1
    assert parsed_with_bom[0]["rule_id"] == "bdi-spike"


def test_unknown_column_emits_warning_and_is_ignored() -> None:
    """An unknown column must NEVER be silently dropped — the operator
    needs to know their CSV has stale headers. The valid columns
    still import; the unknown one is not on the saved dict."""
    # Author the CSV by hand so the stale column is unmistakable.
    csv_text = (
        "rule_id,name,metric,threshold_pct,severity,legacy_field\n"
        "r1,test,bdi,5.0,HIGH,stale\n"
    )
    parsed, warnings = csv_to_rules(csv_text)
    assert len(parsed) == 1
    assert "legacy_field" not in parsed[0]
    assert any("legacy_field" in w for w in warnings)


def test_missing_rule_id_skips_row_with_warning() -> None:
    """A row with no rule_id cannot be persisted — pre-empt the
    engine's save_rules drop with a clear warning so the operator
    knows what happened."""
    csv_text = (
        "rule_id,name,metric,threshold_pct,severity\n"
        ",no-id-rule,bdi,5.0,HIGH\n"
        "good,ok,bdi,5.0,HIGH\n"
    )
    parsed, warnings = csv_to_rules(csv_text)
    assert len(parsed) == 1
    assert parsed[0]["rule_id"] == "good"
    assert any("missing rule_id" in w for w in warnings)


def test_malformed_csv_returns_empty_list_with_error() -> None:
    """Top-level parse error collapses to ``([], [error_msg])`` — the
    CLI / UI rely on this contract to surface the error without
    crashing."""
    parsed, warnings = csv_to_rules(123)  # not a string
    assert parsed == []
    assert len(warnings) == 1
    assert "could not parse" in warnings[0].lower() or "not a string" in warnings[0].lower()


def test_threshold_pct_coerced_from_string_to_float() -> None:
    """CSV cells are always strings — the coercion to float must
    happen on parse so the engine sees a numeric threshold."""
    csv_text = (
        "rule_id,name,metric,threshold_pct,severity\n"
        "r1,test,bdi,5.5,HIGH\n"
    )
    parsed, _ = csv_to_rules(csv_text)
    assert isinstance(parsed[0]["threshold_pct"], float)
    assert parsed[0]["threshold_pct"] == 5.5


def test_booleans_coerce_from_multiple_forms() -> None:
    """A hand-edited CSV from Excel may carry booleans as
    True/False/1/0/yes/no in any case. All canonical forms must
    coerce to the right Python bool — and an unknown value warns +
    defaults instead of silently coercing via truthiness."""
    csv_text = (
        "rule_id,name,metric,threshold_pct,severity,enabled,email_notify,"
        "flap_detection_enabled\n"
        "r1,a,bdi,5.0,HIGH,true,False,1\n"
        "r2,b,bdi,5.0,HIGH,True,no,0\n"
        "r3,c,bdi,5.0,HIGH,1,yes,false\n"
    )
    parsed, _ = csv_to_rules(csv_text)
    assert parsed[0]["enabled"] is True
    assert parsed[0]["email_notify"] is False
    assert parsed[0]["flap_detection_enabled"] is True
    assert parsed[1]["enabled"] is True
    assert parsed[1]["email_notify"] is False
    assert parsed[1]["flap_detection_enabled"] is False
    assert parsed[2]["enabled"] is True
    assert parsed[2]["email_notify"] is True
    assert parsed[2]["flap_detection_enabled"] is False


def test_invalid_severity_skips_row_with_warning() -> None:
    """Severity must be CRITICAL/HIGH/MEDIUM/LOW. An unknown value
    gets the row REJECTED, not accepted with a default — silently
    routing an unknown severity would bypass the channel-threshold
    check."""
    csv_text = (
        "rule_id,name,metric,threshold_pct,severity\n"
        "r1,test,bdi,5.0,BOGUS\n"
    )
    parsed, warnings = csv_to_rules(csv_text)
    assert parsed == []
    assert any("BOGUS" in w for w in warnings)


def test_target_channels_split_on_pipe() -> None:
    """Explicit pin: a multi-channel cell splits on ``|`` only. A
    comma inside the cell would be impossible (it would have torn the
    row), but a pipe-only delimiter is the contract."""
    csv_text = (
        "rule_id,name,metric,threshold_pct,severity,target_channels\n"
        "r1,test,bdi,5.0,HIGH,a|b|c\n"
        "r2,empty,bdi,5.0,HIGH,\n"
        "r3,trailing,bdi,5.0,HIGH,a|\n"
    )
    parsed, _ = csv_to_rules(csv_text)
    assert parsed[0]["target_channels"] == ["a", "b", "c"]
    assert parsed[1]["target_channels"] == []
    # Trailing pipe with empty token → just ['a'], not ['a', ''].
    assert parsed[2]["target_channels"] == ["a"]


# ─── validate_rule_csv_row ────────────────────────────────────────────────

def test_validate_rule_csv_row_happy_path() -> None:
    """A well-formed row dict returns (True, 'ok')."""
    ok, reason = validate_rule_csv_row(_full_rule())
    assert ok is True
    assert reason == "ok"


def test_validate_rule_csv_row_rejects_missing_rule_id() -> None:
    """No id → no save. Validator catches it before the round-trip
    through the engine."""
    ok, reason = validate_rule_csv_row({"name": "x", "metric": "bdi"})
    assert ok is False
    assert "rule_id" in reason


def test_validate_rule_csv_row_rejects_bad_severity() -> None:
    """Bad severity is the validator's other hard-no — same reason
    as the parser's reject path."""
    ok, reason = validate_rule_csv_row({"rule_id": "r1", "severity": "Critical-ish"})
    assert ok is False
    assert "severity" in reason


# ─── Round-trip preservation pin ──────────────────────────────────────────

def test_round_trip_preserves_every_field() -> None:
    """Full-field round-trip pin: a rule carrying every column must
    re-emerge from rules_to_csv → csv_to_rules with every field equal
    to the input. Catches drift in column order, coercion, or
    omission across the entire schema in one shot."""
    rule = _full_rule()
    rule["cooldown_minutes"] = 1440
    rule["flap_detection_enabled"] = True
    rule["flap_window_minutes"] = 60
    rule["flap_threshold_crossings"] = 4
    rule["target_channels"] = ["alpha", "beta"]
    rule["email_notify"] = True
    rule["enabled"] = False
    csv_text = rules_to_csv([rule])
    parsed, warnings = csv_to_rules(csv_text)
    assert warnings == []
    p = parsed[0]
    assert p["rule_id"] == rule["rule_id"]
    assert p["name"] == rule["name"]
    assert p["metric"] == rule["metric"]
    assert p["threshold_pct"] == rule["threshold_pct"]
    assert p["severity"] == rule["severity"]
    assert p["condition"] == rule["condition"]
    assert p["enabled"] is False
    assert p["email_notify"] is True
    assert p["target_channels"] == ["alpha", "beta"]
    assert p["cooldown_minutes"] == 1440
    assert p["flap_detection_enabled"] is True
    assert p["flap_window_minutes"] == 60
    assert p["flap_threshold_crossings"] == 4
