"""Tests for ui.tab_rule_history — the per-rule history view.

Coverage
--------
- Module imports cleanly under the mock_streamlit fixture.
- render() returns without raising when no rules exist (empty state).
- render() returns without raising when a rule is selected but has no fires.
- render() returns without raising when fires + audit events are seeded.
- A single section failing does not take down the whole tab (per-section
  exception isolation — a monkeypatched engine helper raises, other
  sections still render).
- _compute_kpis reflects the seeded data accurately (totals + ack rate
  + mean TTA).
- _render_recent_fires caps at _RECENT_FIRES_CAP (defensive trim).
- Ack-rate computation handles divide-by-zero (no fires → 0% not NaN).
- _query_rule_audit filters strictly to rule_* actions for the right
  entity_id and ignores unrelated audit events.
- _render_csv_download only emits the button when at least one fire
  exists for the rule.

All tests use the ``mock_streamlit`` fixture from conftest.py so no real
Streamlit runtime is needed. SQLite is isolated to a tmp_path per test
so we never touch the real ship_tracker.db.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

from engine import alert_engine_v2 as engv2
from engine.alert_engine_v2 import (
    ShippingAlert,
    _make,
    acknowledge_alert,
    save_alerts,
    save_rules,
)


# ─── Fixture: isolate persistence per test ────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db. Mirrors the fixture in
    test_alert_engine_v2_rules.py."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ───────────────────────────────────────────────────────────────

def _reload_tab():
    """Import-or-reload so the tab picks up the active mock_streamlit
    fixture even when an earlier test imported it under the real
    streamlit."""
    if "ui.tab_rule_history" in sys.modules:
        return importlib.reload(sys.modules["ui.tab_rule_history"])
    return importlib.import_module("ui.tab_rule_history")


def _seed_rules(count: int = 1) -> list[dict]:
    """Persist ``count`` rules and return the list. Uses simple,
    predictable rule_ids so tests can reference them directly."""
    rules: list[dict] = []
    for i in range(count):
        rules.append({
            "rule_id": f"rule_{i+1}",
            "name": f"Test Rule {i+1}",
            "metric": "Freight Rate",
            "threshold": 5.0 + i,
            "severity": "HIGH",
            "cooldown_minutes": 0,
            "flap_detection_enabled": False,
        })
    save_rules(rules)
    return rules


def _seed_fires(rule_id: str, n: int = 3, *,
                acked: int = 0, base: datetime | None = None) -> list[ShippingAlert]:
    """Persist ``n`` distinct alerts under ``rule_id``. Acks the first
    ``acked`` of them (with a stamped ack timestamp via
    ``acknowledge_alert`` which stamps acknowledged_at)."""
    base = base or datetime.now(timezone.utc)
    out: list[ShippingAlert] = []
    for i in range(n):
        a = _make("MACRO", "HIGH", f"title-{i}", "body")
        # Distinct ticker per alert so v14 dedup_key does not collapse them.
        a.ticker = f"TKR{i}"
        a.created_at = (base - timedelta(hours=i)).isoformat()
        out.append(a)
    save_alerts(out, rule_id=rule_id)
    # Ack the FIRST `acked` alerts (most recent — index 0 has the
    # newest timestamp because we counted backwards on the offset).
    for i in range(min(acked, len(out))):
        acknowledge_alert(out[i].alert_id)
    return out


# ─── Module import + smoke ────────────────────────────────────────────────

def test_module_imports_cleanly(mock_streamlit) -> None:
    """Importing the tab must not raise."""
    mod = _reload_tab()
    assert hasattr(mod, "render")
    assert callable(mod.render)


def test_render_no_rules_does_not_raise(mock_streamlit) -> None:
    """Empty rules list → tab renders an info banner + early-returns
    without exception."""
    mod = _reload_tab()
    # No rules seeded → _load_rules() returns []; render() should
    # render the "no rules" info banner and return cleanly.
    mod.render()


def test_render_rule_with_no_fires_does_not_raise(mock_streamlit) -> None:
    """A rule exists but has zero persisted fires → the tab renders
    every section's "no fires" branch without exception."""
    _seed_rules(count=1)
    mod = _reload_tab()
    mod.render()


def test_render_with_seeded_fires_does_not_raise(mock_streamlit) -> None:
    """A rule with multiple fires + some acked + an audit event renders
    every section without exception."""
    rules = _seed_rules(count=1)
    rid = rules[0]["rule_id"]
    _seed_fires(rid, n=5, acked=2)
    # Record an audit event for this rule so the audit panel has a row.
    try:
        from auth.audit import record_audit
        record_audit("rule_create", entity_type="alert_rule",
                     entity_id=rid, detail={"name": "Test Rule 1"})
    except Exception:
        pass
    mod = _reload_tab()
    mod.render()


# ─── Per-section exception isolation ─────────────────────────────────────

def test_section_failure_does_not_take_down_tab(mock_streamlit,
                                                monkeypatch) -> None:
    """When one engine helper raises, the tab still renders the rest of
    the sections. We monkeypatch ``_query_rule_audit`` to raise and
    verify render() still completes without propagating the exception.
    """
    rules = _seed_rules(count=1)
    rid = rules[0]["rule_id"]
    _seed_fires(rid, n=3, acked=1)
    mod = _reload_tab()

    def _boom(*args, **kwargs):
        raise RuntimeError("boom from audit helper")

    monkeypatch.setattr(mod, "_query_rule_audit", _boom)
    # Must not raise even though _query_rule_audit explodes inside the
    # audit-trail section.
    mod.render()


# ─── KPI computation correctness ─────────────────────────────────────────

def test_compute_kpis_reflects_seeded_data(mock_streamlit) -> None:
    """KPIs match the seeded counts + ack rate."""
    rules = _seed_rules(count=1)
    rid = rules[0]["rule_id"]
    # Seed 4 fires within the last hour; ack 2 of them.
    fires = _seed_fires(rid, n=4, acked=2)
    mod = _reload_tab()
    alerts_all = mod._get_alerts_by_rule(rid, limit=500)
    alerts_7d = mod._get_alerts_by_rule(rid, since=(
        datetime.now(timezone.utc) - timedelta(days=7)
    ).isoformat(), limit=500)
    alerts_30d = mod._get_alerts_by_rule(rid, since=(
        datetime.now(timezone.utc) - timedelta(days=30)
    ).isoformat(), limit=500)
    full_rows_30d = mod._get_full_rows_by_rule(rid, limit=500)
    kpis = mod._compute_kpis(rid, alerts_30d, alerts_7d, alerts_all,
                             full_rows_30d)
    assert kpis["total_all"] == 4
    assert kpis["total_7d"] == 4
    assert kpis["total_30d"] == 4
    # 2 of 4 acked → 50%.
    assert kpis["ack_rate_pct"] == pytest.approx(50.0)
    # TTA computed for the acked rows — they were acked moments after
    # creation (synchronous tests). Mean is small but non-None.
    assert kpis["mean_tta_min"] is not None
    assert kpis["mean_tta_min"] >= 0.0
    # Suppression counters: never seeded → 0.
    assert kpis["suppressed_cooldown"] == 0
    assert kpis["suppressed_flap"] == 0
    assert kpis["suppressed_silence"] == 0


def test_ack_rate_handles_divide_by_zero(mock_streamlit) -> None:
    """No fires → ack rate is 0.0 (NOT NaN)."""
    mod = _reload_tab()
    kpis = mod._compute_kpis(
        rule_id="ghost",
        alerts_30d=[], alerts_7d=[], alerts_all=[], full_rows_30d=[],
    )
    assert kpis["ack_rate_pct"] == 0.0
    # And it must be a regular float, not NaN.
    import math
    assert not math.isnan(kpis["ack_rate_pct"])


# ─── Recent fires table is capped at _RECENT_FIRES_CAP ───────────────────

def test_recent_fires_caps_at_limit(mock_streamlit) -> None:
    """Even when more than 50 fires exist for the rule, the recent-fires
    fetch returns at most _RECENT_FIRES_CAP rows."""
    rules = _seed_rules(count=1)
    rid = rules[0]["rule_id"]
    # Build a batch with distinct tickers so v14 dedup does not
    # collapse them. _MAX_STORED is 500 in production; the cap on
    # the recent fetch is independent of it.
    base = datetime.now(timezone.utc)
    out = []
    for i in range(60):
        a = _make("MACRO", "HIGH", f"title-{i}", "body")
        a.ticker = f"TKR{i:03d}"
        a.created_at = (base - timedelta(seconds=i)).isoformat()
        out.append(a)
    save_alerts(out, rule_id=rid)
    mod = _reload_tab()
    rows = mod._get_full_rows_by_rule(rid, limit=mod._RECENT_FIRES_CAP)
    assert len(rows) == mod._RECENT_FIRES_CAP
    assert mod._RECENT_FIRES_CAP == 50


# ─── Audit trail filters strictly to rule_* actions ──────────────────────

def test_audit_trail_filters_to_rule_actions_only(mock_streamlit) -> None:
    """Audit events whose action does NOT start with 'rule_' are excluded
    even when their entity_id matches the rule_id."""
    rules = _seed_rules(count=1)
    rid = rules[0]["rule_id"]
    from auth.audit import record_audit
    # Rule-scoped event we DO want.
    record_audit("rule_create", entity_type="alert_rule",
                 entity_id=rid, detail={"k": "v"})
    record_audit("rule_edit", entity_type="alert_rule",
                 entity_id=rid, detail={"k": "v2"})
    # An unrelated event with the SAME entity_id but a different action.
    # The filter must drop this row because the action prefix is not
    # 'rule_'. It is a synthetic case (the entity_id collision would
    # not happen in practice given UUIDs), but it precisely exercises
    # the prefix-filter contract.
    record_audit("ack_alert", entity_type="alert",
                 entity_id=rid, detail={"k": "ignored"})
    # A rule-scoped event for a DIFFERENT rule_id; must also be dropped.
    record_audit("rule_create", entity_type="alert_rule",
                 entity_id="other_rule", detail={"k": "v"})
    mod = _reload_tab()
    events = mod._query_rule_audit(rid)
    actions = sorted(getattr(ev, "action", "") for ev in events)
    assert actions == ["rule_create", "rule_edit"]


# ─── CSV download only when fires exist ──────────────────────────────────

def test_csv_download_only_when_fires_exist(mock_streamlit, monkeypatch) -> None:
    """The CSV button is only rendered when the rule has at least one
    fire. We probe by spying on st.download_button via the mock."""
    rules = _seed_rules(count=1)
    rid = rules[0]["rule_id"]
    mod = _reload_tab()

    download_calls: list[tuple] = []

    def _spy_download(*args, **kwargs):
        download_calls.append((args, kwargs))
        return False

    # Path A: zero fires → button must NOT be emitted.
    monkeypatch.setattr(mod.st, "download_button", _spy_download)
    mod._render_csv_download(rid, "Test Rule", alerts=[], full_rows=[])
    assert download_calls == []

    # Path B: at least one fire → button IS emitted.
    fires = _seed_fires(rid, n=2, acked=1)
    full_rows = mod._get_full_rows_by_rule(rid, limit=mod._RECENT_FIRES_CAP)
    alerts_all = mod._get_alerts_by_rule(rid, limit=500)
    monkeypatch.setattr(mod.st, "download_button", _spy_download)
    mod._render_csv_download(rid, "Test Rule",
                             alerts=alerts_all, full_rows=full_rows)
    assert len(download_calls) == 1
    # Sanity-check the CSV payload carries the expected columns.
    args, kwargs = download_calls[0]
    payload: bytes = kwargs.get("data") or (args[1] if len(args) > 1 else b"")
    assert b"alert_id" in payload
    assert b"acknowledged" in payload
