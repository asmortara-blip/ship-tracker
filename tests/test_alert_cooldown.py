"""Tests for engine.alert_engine_v2 per-rule cooldown (v18).

An AlertRule whose condition stays tripped previously fired on every
evaluation, spamming downstream channels. v18 adds a per-rule
``cooldown_minutes`` field that suppresses repeat fires of the SAME
rule_id (per user) for N minutes after a successful fire. Cooldown is
orthogonal to the v14 dedup_key collapse — dedup merges bounces of an
alert that already fired; cooldown stops the rule from firing in the
first place.

Covers:
  - cooldown_minutes = 0 → fires every evaluation (legacy behaviour
    preserved for rules without the new field).
  - cooldown_minutes = 60 → first fire OK, second fire 30 min later
    SUPPRESSED, third fire 70 min later OK.
  - Per-user isolation: alice's fire of rule X does NOT suppress bob's
    fire of the same rule_id.
  - Cooldown is per-rule_id, not per-(rule_id, ticker). A multi-ticker
    rule that fires for ZIM at t=0 still suppresses MATX at t=30 with
    cooldown=60.
  - normalize_rule coerces cooldown_minutes: -5 → 0 (clamp), '10' → 10
    (str), 'abc' → 0 (fallback), None → 0, missing → 0.
  - save_rules + load_rules round-trip the field through the JSON blob.
  - A pre-v18 rule (no cooldown_minutes key) loads with the field
    defaulting to 0 once normalized.
  - The suppressed_by_cooldown counter increments on every suppression
    and is observable via get_suppressed_by_cooldown_count().
  - fire_rule with empty rule_id is a no-op gate (the cooldown check
    short-circuits to False — defensive guard for direct callers).
  - rule_id is stamped on the alerts row when fire_rule persists.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine import alert_engine_v2 as engv2
from engine.alert_engine_v2 import (
    ShippingAlert,
    _in_cooldown,
    _make,
    fire_rule,
    get_suppressed_by_cooldown_count,
    load_rules,
    normalize_rule,
    reset_rules,
    save_alerts,
    save_rules,
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
    route_id: str = "",
    port_locode: str = "",
    value: float = 1000.0,
    change_pct: float = 5.0,
) -> ShippingAlert:
    """Build a ShippingAlert with sane defaults — mirrors the helper in
    test_alert_dedup so cooldown tests read in the same shape."""
    return _make(
        alert_type, severity, "title", "body",
        ticker=ticker, route_id=route_id, port_locode=port_locode,
        value=value, threshold=5.0, change_pct=change_pct,
    )


def _rule(rule_id: str = "rule_test", cooldown_minutes: int = 0) -> dict:
    """Build a minimal rule dict in the shape ``save_rules`` accepts."""
    return {
        "rule_id": rule_id,
        "name": f"Test rule {rule_id}",
        "metric": "Freight Rate",
        "threshold": 5.0,
        "condition": "Above",
        "severity": "HIGH",
        "enabled": True,
        "cooldown_minutes": cooldown_minutes,
    }


def _count_rows_for_rule(rule_id: str) -> int:
    """Count alerts rows stamped with this rule_id."""
    from state.db import get_connection
    return int(
        get_connection().execute(
            "SELECT COUNT(*) FROM alerts WHERE rule_id = ?", (rule_id,)
        ).fetchone()[0]
    )


def _stamp_last_fire(rule_id: str, *, minutes_ago: float, user_id: str = "") -> None:
    """Backdate every alert for ``rule_id`` so the cooldown gate sees the
    most-recent fire as ``minutes_ago`` minutes ago. Used by tests that
    want to express "the rule fired N minutes ago" without sleeping the
    real wall clock.

    Updates both created_at AND last_fired_at — the cooldown query uses
    MAX(COALESCE(last_fired_at, created_at)), so we move both to the
    same backdated timestamp so the helper is unambiguous regardless of
    which column drives the comparison.
    """
    from state.db import get_connection

    backdated = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat()
    conn = get_connection()
    with conn:
        conn.execute(
            """
            UPDATE alerts
            SET created_at    = ?,
                last_fired_at = ?
            WHERE rule_id = ? AND user_id = ?
            """,
            (backdated, backdated, rule_id, user_id),
        )


# ─── normalize_rule cooldown coercion ──────────────────────────────────────

def test_normalize_rule_coerces_negative_to_zero() -> None:
    """A negative cooldown is nonsensical — clamp to 0 (no cooldown)
    rather than letting a stray minus sign turn into "fire forever"."""
    r = _rule(cooldown_minutes=-5)
    normalize_rule(r)
    assert r["cooldown_minutes"] == 0


def test_normalize_rule_coerces_numeric_string_to_int() -> None:
    """A blob round-tripped through JSON or a hand-edited UI value
    may carry the cooldown as a string."""
    r = _rule()
    r["cooldown_minutes"] = "10"
    normalize_rule(r)
    assert r["cooldown_minutes"] == 10
    assert isinstance(r["cooldown_minutes"], int)


def test_normalize_rule_coerces_garbage_string_to_zero() -> None:
    """A non-numeric string falls back to 0 — better to silently
    disable cooldown than to crash the engine on a typo."""
    r = _rule()
    r["cooldown_minutes"] = "abc"
    normalize_rule(r)
    assert r["cooldown_minutes"] == 0


def test_normalize_rule_coerces_none_to_zero() -> None:
    """An explicit None (or a missing-key fallback that returns None)
    coerces to 0 — same defensive posture as the garbage-string case."""
    r = _rule()
    r["cooldown_minutes"] = None
    normalize_rule(r)
    assert r["cooldown_minutes"] == 0


def test_normalize_rule_backfills_missing_cooldown() -> None:
    """A pre-v18 blob without the cooldown_minutes key picks up the
    v18 default (0 = no cooldown) when normalized."""
    r = {"rule_id": "old_rule", "name": "old", "threshold": 5.0}
    normalize_rule(r)
    assert r["cooldown_minutes"] == 0


def test_normalize_rule_passes_through_valid_int() -> None:
    """A clean int value is preserved exactly — the coercion path
    must be transparent when the input is already well-typed."""
    r = _rule(cooldown_minutes=60)
    normalize_rule(r)
    assert r["cooldown_minutes"] == 60


# ─── save_rules + load_rules round-trip ────────────────────────────────────

def test_save_rules_load_rules_round_trips_cooldown_minutes() -> None:
    """The cooldown_minutes field survives the JSON-blob round-trip
    through the alert_rules table."""
    save_rules([_rule(rule_id="r1", cooldown_minutes=45)], user_id="")
    loaded = load_rules(user_id="")
    assert len(loaded) == 1
    assert loaded[0]["cooldown_minutes"] == 45


def test_save_rules_load_rules_back_compat_missing_cooldown() -> None:
    """A rule blob saved WITHOUT cooldown_minutes loads cleanly and
    normalize_rule defaults the field to 0 — the v18 contract is
    that existing rules opt out of cooldown."""
    raw = {
        "rule_id": "old_rule",
        "name": "Old rule with no cooldown",
        "threshold": 5.0,
        "severity": "HIGH",
        "enabled": True,
    }
    save_rules([raw], user_id="")
    loaded = load_rules(user_id="")
    assert len(loaded) == 1
    # Raw load is byte-exact (no auto-normalization in load_rules).
    assert "cooldown_minutes" not in loaded[0]
    # After normalize_rule, the field is present and defaults to 0.
    normalize_rule(loaded[0])
    assert loaded[0]["cooldown_minutes"] == 0


# ─── _in_cooldown direct ───────────────────────────────────────────────────

def test_in_cooldown_zero_minutes_always_false() -> None:
    """cooldown_minutes=0 means "no cooldown" — return False without
    even hitting the DB."""
    assert _in_cooldown("any_rule", 0, user_id="") is False


def test_in_cooldown_negative_minutes_always_false() -> None:
    """Negative values are treated like 0 (no cooldown). normalize_rule
    would have clamped these out already, but _in_cooldown is
    defensive in case a caller bypasses normalization."""
    assert _in_cooldown("any_rule", -10, user_id="") is False


def test_in_cooldown_empty_rule_id_false() -> None:
    """An empty rule_id has no meaningful prior to look up — short-
    circuit to False rather than scanning every NULL-rule_id row."""
    assert _in_cooldown("", 60, user_id="") is False


def test_in_cooldown_no_prior_fire_returns_false() -> None:
    """When the rule has never fired (no rows with this rule_id under
    this user), the cooldown gate is clear."""
    assert _in_cooldown("never_fired_rule", 60, user_id="alice") is False


# ─── fire_rule cooldown enforcement ────────────────────────────────────────

def test_fire_rule_zero_cooldown_fires_every_time() -> None:
    """Rule with cooldown_minutes=0 fires on every evaluation —
    matches the pre-v18 legacy behaviour."""
    rule = _rule(rule_id="r1", cooldown_minutes=0)
    # Fire 5 times in quick succession. Each fire returns True.
    for i in range(5):
        fired = fire_rule(rule, [_alert(ticker=f"T{i}")], user_id="")
        assert fired is True
    # All 5 alerts persisted under rule_id="r1". (Distinct tickers so
    # the v14 dedup_key does not collapse them into one row.)
    assert _count_rows_for_rule("r1") == 5


def test_fire_rule_within_window_suppressed() -> None:
    """Rule with cooldown_minutes=60: first fire OK, second fire 30
    min later SUPPRESSED, third fire 70 min later OK."""
    rule = _rule(rule_id="r1", cooldown_minutes=60)

    # First fire at t=0 — permitted.
    assert fire_rule(rule, [_alert(ticker="A")], user_id="") is True
    assert _count_rows_for_rule("r1") == 1

    # Backdate so the prior fire looks 30 min old. Second fire
    # WITHIN the 60-min cooldown window → suppressed.
    _stamp_last_fire("r1", minutes_ago=30, user_id="")
    assert fire_rule(rule, [_alert(ticker="B")], user_id="") is False
    # Suppressed = no new row inserted.
    assert _count_rows_for_rule("r1") == 1

    # Backdate again so the prior fire looks 70 min old. Third fire
    # OUTSIDE the 60-min cooldown window → permitted.
    _stamp_last_fire("r1", minutes_ago=70, user_id="")
    assert fire_rule(rule, [_alert(ticker="C")], user_id="") is True
    assert _count_rows_for_rule("r1") == 2


def test_fire_rule_per_user_isolation() -> None:
    """Alice's cooldown does NOT suppress Bob's identical rule."""
    rule = _rule(rule_id="r1", cooldown_minutes=60)

    # Alice fires, then her fire goes within window.
    assert fire_rule(rule, [_alert(ticker="A")], user_id="alice") is True
    _stamp_last_fire("r1", minutes_ago=30, user_id="alice")
    # Alice's second attempt is suppressed.
    assert fire_rule(rule, [_alert(ticker="B")], user_id="alice") is False

    # Bob fires the SAME rule_id — must NOT be suppressed by alice's
    # cooldown.
    assert fire_rule(rule, [_alert(ticker="A")], user_id="bob") is True
    # Two distinct rows: one for alice's first fire, one for bob's first.
    assert _count_rows_for_rule("r1") == 2


def test_fire_rule_cooldown_is_per_rule_not_per_ticker() -> None:
    """A multi-ticker rule that fires for ZIM at t=0 must still
    suppress MATX at t=30 with cooldown=60. The cooldown gate is keyed
    on rule_id alone — different tickers do NOT escape it."""
    rule = _rule(rule_id="multi_ticker_rule", cooldown_minutes=60)

    # First fire with ticker=ZIM — permitted.
    assert fire_rule(rule, [_alert(ticker="ZIM")], user_id="") is True
    _stamp_last_fire("multi_ticker_rule", minutes_ago=30, user_id="")

    # Second fire with DIFFERENT ticker=MATX, same rule_id, within
    # cooldown window — SUPPRESSED. The ticker is irrelevant to the
    # cooldown gate.
    assert fire_rule(rule, [_alert(ticker="MATX")], user_id="") is False
    # Only the first row exists.
    assert _count_rows_for_rule("multi_ticker_rule") == 1


# ─── Suppressed counter ────────────────────────────────────────────────────

def test_suppressed_counter_starts_at_zero() -> None:
    """Before any suppression event, the counter reads 0 (the kv_state
    row doesn't even exist yet)."""
    assert get_suppressed_by_cooldown_count() == 0


def test_suppressed_counter_increments_on_each_suppression() -> None:
    """Every cooldown-suppressed fire bumps the counter by 1."""
    rule = _rule(rule_id="r1", cooldown_minutes=60)

    # Prime the cooldown — first fire is permitted, no bump.
    assert fire_rule(rule, [_alert(ticker="A")], user_id="") is True
    assert get_suppressed_by_cooldown_count() == 0

    # Three suppressions in a row.
    _stamp_last_fire("r1", minutes_ago=10, user_id="")
    for _ in range(3):
        assert fire_rule(rule, [_alert(ticker="A")], user_id="") is False

    assert get_suppressed_by_cooldown_count() == 3


def test_suppressed_counter_not_bumped_on_successful_fire() -> None:
    """A permitted fire (cooldown clear) does NOT bump the counter —
    the counter measures suppressions, not evaluations."""
    rule = _rule(rule_id="r1", cooldown_minutes=0)

    for _ in range(5):
        fire_rule(rule, [_alert(ticker="A")], user_id="")
    assert get_suppressed_by_cooldown_count() == 0


# ─── Row stamping ──────────────────────────────────────────────────────────

def test_fire_rule_stamps_rule_id_on_alerts_row() -> None:
    """When fire_rule persists an alert, the row's rule_id column is
    set to the rule's rule_id (not NULL). This is what lets the
    cooldown query find the prior fire."""
    rule = _rule(rule_id="r_stamp", cooldown_minutes=0)
    fire_rule(rule, [_alert(ticker="ZIM")], user_id="")

    from state.db import get_connection
    row = get_connection().execute(
        "SELECT rule_id FROM alerts WHERE ticker = 'ZIM'"
    ).fetchone()
    assert row is not None
    assert row["rule_id"] == "r_stamp"


def test_save_alerts_without_rule_id_leaves_column_null() -> None:
    """The legacy save_alerts path (no rule_id parameter) preserves
    the NULL value in the rule_id column. The cooldown query then
    naturally skips those rows because it filters on rule_id = ?."""
    save_alerts([_alert(ticker="LEGACY")], user_id="")

    from state.db import get_connection
    row = get_connection().execute(
        "SELECT rule_id FROM alerts WHERE ticker = 'LEGACY'"
    ).fetchone()
    assert row is not None
    assert row["rule_id"] is None


def test_in_cooldown_skips_null_rule_id_rows() -> None:
    """A legacy alert with NULL rule_id must NOT be picked up by the
    cooldown query for any non-empty rule_id (the WHERE rule_id = ?
    filter naturally excludes NULL — this test pins that contract)."""
    # Insert a legacy alert (no rule_id stamp).
    save_alerts([_alert(ticker="LEGACY")], user_id="")
    # Cooldown check for a different rule_id sees no priors → False.
    assert _in_cooldown("some_rule", 60, user_id="") is False


# ─── Integration with normalize_rule + fire_rule ───────────────────────────

def test_fire_rule_normalizes_rule_in_place() -> None:
    """fire_rule calls normalize_rule on its input so callers do NOT
    need to pre-normalize. A blob with a stringy cooldown ('30') gates
    correctly."""
    rule = _rule(rule_id="r_norm", cooldown_minutes=0)
    rule["cooldown_minutes"] = "30"  # stringy — pre-normalization

    # First fire — permitted (cooldown=30 → only 0 prior fires exist).
    assert fire_rule(rule, [_alert(ticker="A")], user_id="") is True
    # normalize_rule mutated the dict in-place.
    assert rule["cooldown_minutes"] == 30
    assert isinstance(rule["cooldown_minutes"], int)


def test_fire_rule_handles_empty_alerts_list() -> None:
    """fire_rule with an empty alerts list and a clear cooldown is a
    legitimate 'evaluation produced no alerts' fire — returns True
    (the evaluation happened) without inserting any rows."""
    rule = _rule(rule_id="r_empty", cooldown_minutes=0)
    assert fire_rule(rule, [], user_id="") is True
    assert _count_rows_for_rule("r_empty") == 0


# ─── Cleanup helper coverage ───────────────────────────────────────────────

def test_reset_rules_does_not_clear_suppressed_counter() -> None:
    """reset_rules drops the rules table; the suppressed counter lives
    in kv_state and survives the reset (it is operator telemetry, not
    rule state)."""
    rule = _rule(rule_id="r1", cooldown_minutes=60)
    fire_rule(rule, [_alert(ticker="A")], user_id="")
    _stamp_last_fire("r1", minutes_ago=10, user_id="")
    fire_rule(rule, [_alert(ticker="A")], user_id="")  # suppressed
    assert get_suppressed_by_cooldown_count() == 1

    reset_rules()
    # Counter unaffected.
    assert get_suppressed_by_cooldown_count() == 1
