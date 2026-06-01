"""Tests for AlertRule.target_channels rule → channel routing.

When the alert engine was first built, every enabled delivery channel
saw every alert that met its severity threshold. That worked but made
it impossible to say "geopolitics alerts go to the geopolitics-slack
only". This module pins the routing contract:

  - AlertRule.target_channels = []     →  every enabled channel eligible
                                          (legacy behaviour preserved)
  - AlertRule.target_channels = [...]  →  only channels whose .name is
                                          in the list are eligible
  - channel.enabled = False             →  always filtered out, regardless
                                          of rule targeting

Plus:
  - normalize_rule(dict)  →  defaults missing/invalid target_channels to []
  - deliver_pending_for_rule(rule, alerts, channels)  →  delivers the
    alerts to the eligible channels (severity-gated per-channel as usual)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from engine.alert_delivery import (
    DeliveryChannel,
    DeliveryResult,
    deliver_pending_for_rule,
    filter_channels_by_rule,
)
from engine.alert_engine_v2 import (
    AlertRule,
    _make,
    load_rules,
    normalize_rule,
    save_rules,
)


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite isolation — never touch the real DB."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


def _mk_channel(
    name: str,
    *,
    enabled: bool = True,
    severity_threshold: str = "LOW",
    kind: str = "slack",
    target: str = "https://hooks.slack.com/services/x/y/z",
    digest_mode: str = "immediate",
) -> DeliveryChannel:
    return DeliveryChannel(
        channel_id=f"id-{name}",
        name=name,
        kind=kind,
        target=target,
        severity_threshold=severity_threshold,
        enabled=enabled,
        created_at="2026-05-22T00:00:00+00:00",
        digest_mode=digest_mode,
    )


# ─── AlertRule dataclass shape ────────────────────────────────────────────

def test_alert_rule_default_target_channels_is_empty_list() -> None:
    """Default factory must produce an empty list — and a fresh list per
    instance so mutating one rule's list doesn't poison another."""
    r1 = AlertRule(rule_id="r1", name="n", alert_type="BDI_MOVE",
                   enabled=True, threshold=5.0, severity="HIGH")
    r2 = AlertRule(rule_id="r2", name="n", alert_type="BDI_MOVE",
                   enabled=True, threshold=5.0, severity="HIGH")
    assert r1.target_channels == []
    r1.target_channels.append("slack-prod")
    assert r2.target_channels == []  # independent


def test_alert_rule_with_target_channels() -> None:
    r = AlertRule(
        rule_id="r", name="n", alert_type="BDI_MOVE",
        enabled=True, threshold=5.0, severity="HIGH",
        target_channels=["slack-prod", "email-team"],
    )
    assert r.target_channels == ["slack-prod", "email-team"]


# ─── normalize_rule ──────────────────────────────────────────────────────

def test_normalize_rule_adds_missing_target_channels() -> None:
    """Legacy rule blobs lack target_channels — normalize fills with []."""
    rule = {"rule_id": "r", "name": "n", "enabled": True}
    out = normalize_rule(rule)
    assert out["target_channels"] == []


def test_normalize_rule_preserves_valid_target_channels() -> None:
    rule = {"rule_id": "r", "target_channels": ["a", "b"]}
    assert normalize_rule(rule)["target_channels"] == ["a", "b"]


def test_normalize_rule_strips_non_string_entries() -> None:
    """A hand-edited blob with mixed types must be coerced safely."""
    rule = {"rule_id": "r", "target_channels": ["a", 5, None, "b"]}
    assert normalize_rule(rule)["target_channels"] == ["a", "b"]


def test_normalize_rule_replaces_non_list_with_empty_list() -> None:
    rule = {"rule_id": "r", "target_channels": "not a list"}
    assert normalize_rule(rule)["target_channels"] == []


def test_normalize_rule_passes_through_non_dict() -> None:
    """Non-dict input is returned unchanged so callers can chain safely."""
    assert normalize_rule("garbage") == "garbage"
    assert normalize_rule(None) is None
    assert normalize_rule(42) == 42


# ─── filter_channels_by_rule ─────────────────────────────────────────────

def test_filter_channels_empty_target_channels_returns_all_enabled() -> None:
    """Legacy behaviour: empty target_channels means broadcast to every
    enabled channel."""
    a = _mk_channel("slack-prod")
    b = _mk_channel("email-team")
    rule = {"rule_id": "r", "target_channels": []}
    assert filter_channels_by_rule(rule, [a, b]) == [a, b]


def test_filter_channels_missing_target_channels_returns_all_enabled() -> None:
    """A rule loaded from pre-routing JSON has no target_channels key —
    treat it the same as empty (legacy broadcast)."""
    a = _mk_channel("slack-prod")
    rule = {"rule_id": "r"}  # no target_channels at all
    assert filter_channels_by_rule(rule, [a]) == [a]


def test_filter_channels_non_empty_target_channels_filters_by_name() -> None:
    a = _mk_channel("slack-prod")
    b = _mk_channel("email-team")
    c = _mk_channel("slack-noise")
    rule = {"rule_id": "r", "target_channels": ["slack-prod", "email-team"]}
    out = filter_channels_by_rule(rule, [a, b, c])
    assert {ch.name for ch in out} == {"slack-prod", "email-team"}


def test_filter_channels_with_nonexistent_targets_returns_empty() -> None:
    a = _mk_channel("slack-prod")
    rule = {"rule_id": "r", "target_channels": ["does-not-exist"]}
    assert filter_channels_by_rule(rule, [a]) == []


def test_filter_channels_disabled_channel_filtered_out_even_when_targeted() -> None:
    """Disabling a channel wins over rule targeting — a paused channel
    never gets delivered to, no matter how the rule is configured."""
    disabled = _mk_channel("slack-prod", enabled=False)
    enabled = _mk_channel("email-team")
    rule = {"rule_id": "r", "target_channels": ["slack-prod", "email-team"]}
    out = filter_channels_by_rule(rule, [disabled, enabled])
    assert [ch.name for ch in out] == ["email-team"]


def test_filter_channels_accepts_alert_rule_object() -> None:
    """The function works with either a dict or an AlertRule dataclass."""
    a = _mk_channel("slack-prod")
    rule = AlertRule(
        rule_id="r", name="n", alert_type="BDI_MOVE",
        enabled=True, threshold=5.0, severity="HIGH",
        target_channels=["slack-prod"],
    )
    assert filter_channels_by_rule(rule, [a]) == [a]


# ─── deliver_pending_for_rule ─────────────────────────────────────────────

def _patch_immediate_delivery():
    """Patch deliver_alert to a no-op success so we test the routing
    layer in isolation. Each call returns the standard 200 success
    result regardless of channel kind or alert content."""
    return patch(
        "engine.alert_delivery.deliver_alert",
        return_value=DeliveryResult(success=True, status_code=200, error_msg=""),
    )


def test_deliver_pending_for_rule_routes_to_targeted_channels_only() -> None:
    """3 channels, rule targets 2 of them — only 2 get delivered to."""
    a = _mk_channel("slack-prod")
    b = _mk_channel("email-team", kind="slack",
                    target="https://hooks.slack.com/services/x/y/z")  # use slack so we can mock one path
    c = _mk_channel("slack-noise")
    rule = {"rule_id": "r", "target_channels": ["slack-prod", "email-team"]}
    alert = _make("BDI_MOVE", "HIGH", "t", "b")

    with _patch_immediate_delivery():
        results = deliver_pending_for_rule(rule, [alert], [a, b, c])

    # 2 channels × 1 alert = 2 results
    assert len(results) == 2
    assert all(r.success for r in results)


def test_deliver_pending_for_rule_empty_targets_broadcasts_to_all() -> None:
    a = _mk_channel("slack-prod")
    b = _mk_channel("email-team", kind="slack",
                    target="https://hooks.slack.com/services/x/y/z")
    rule = {"rule_id": "r", "target_channels": []}
    alert = _make("BDI_MOVE", "HIGH", "t", "b")

    with _patch_immediate_delivery():
        results = deliver_pending_for_rule(rule, [alert], [a, b])

    assert len(results) == 2


def test_deliver_pending_for_rule_respects_severity_threshold() -> None:
    """Per-channel threshold still applies — a LOW alert never reaches
    a HIGH-only channel even when the rule targets it."""
    high_only = _mk_channel("slack-prod", severity_threshold="HIGH")
    rule = {"rule_id": "r", "target_channels": ["slack-prod"]}
    low_alert = _make("BDI_MOVE", "LOW", "t", "b")

    with _patch_immediate_delivery():
        results = deliver_pending_for_rule(rule, [low_alert], [high_only])

    # Channel exists + targeted, but the LOW alert doesn't clear the bar
    assert results == []


def test_deliver_pending_for_rule_skips_disabled_channels() -> None:
    """Disabled channels filtered out even when the rule targets them."""
    disabled = _mk_channel("slack-prod", enabled=False)
    rule = {"rule_id": "r", "target_channels": ["slack-prod"]}
    alert = _make("BDI_MOVE", "HIGH", "t", "b")

    with _patch_immediate_delivery():
        results = deliver_pending_for_rule(rule, [alert], [disabled])

    assert results == []


def test_deliver_pending_for_rule_daily_digest_collapses_per_channel() -> None:
    """digest_mode='daily' → one deliver_digest call per channel,
    regardless of alert count. With 3 alerts × 1 channel = 1 result."""
    daily = _mk_channel("slack-prod", digest_mode="daily")
    rule = {"rule_id": "r", "target_channels": ["slack-prod"]}
    alerts = [_make("BDI_MOVE", "HIGH", f"t{i}", "b") for i in range(3)]

    with patch(
        "engine.alert_delivery.deliver_digest",
        return_value=DeliveryResult(success=True, status_code=200, error_msg=""),
    ) as digest_mock:
        results = deliver_pending_for_rule(rule, alerts, [daily])

    assert len(results) == 1
    assert digest_mock.call_count == 1
    # The single call carries all 3 alerts
    args, _ = digest_mock.call_args
    assert len(args[1]) == 3


def test_deliver_pending_for_rule_daily_empty_alerts_no_call() -> None:
    """digest_mode='daily' with zero matching alerts → no delivery."""
    daily = _mk_channel("slack-prod", digest_mode="daily")
    rule = {"rule_id": "r", "target_channels": ["slack-prod"]}

    with patch("engine.alert_delivery.deliver_digest") as digest_mock:
        results = deliver_pending_for_rule(rule, [], [daily])

    assert results == []
    assert digest_mock.call_count == 0


def test_deliver_pending_for_rule_mixed_immediate_and_daily_channels() -> None:
    """When a rule targets one immediate channel and one daily channel,
    we get 1 result per alert from immediate + 1 digest result from daily."""
    imm = _mk_channel("slack-prod")
    daily = _mk_channel("email-team", kind="slack",
                        target="https://hooks.slack.com/services/x/y/z",
                        digest_mode="daily")
    rule = {"rule_id": "r", "target_channels": ["slack-prod", "email-team"]}
    alerts = [_make("BDI_MOVE", "HIGH", f"t{i}", "b") for i in range(2)]

    with _patch_immediate_delivery(), patch(
        "engine.alert_delivery.deliver_digest",
        return_value=DeliveryResult(success=True, status_code=200, error_msg=""),
    ):
        results = deliver_pending_for_rule(rule, alerts, [imm, daily])

    # 2 from immediate (one per alert) + 1 from daily = 3
    assert len(results) == 3


# ─── Backward-compat round-trip ──────────────────────────────────────────

def test_save_and_load_rules_round_trip_with_target_channels() -> None:
    """save_rules + load_rules persist target_channels correctly when set."""
    rules = [
        {
            "id": "r1",
            "name": "Geopolitics alerts",
            "metric": "BDI",
            "threshold": 5.0,
            "condition": "Above",
            "severity": "Critical",
            "enabled": True,
            "target_channels": ["slack-geo", "email-desk"],
        },
    ]
    save_rules(rules)
    loaded = load_rules()
    assert len(loaded) == 1
    assert loaded[0]["target_channels"] == ["slack-geo", "email-desk"]


def test_load_rules_returns_legacy_blob_without_normalizing() -> None:
    """A pre-routing rule blob (no target_channels field) loads as-saved.
    Callers that need a normalized shape run it through normalize_rule()
    — load_rules() itself doesn't auto-normalize so the round-trip
    contract stays byte-exact."""
    rules = [{"id": "r1", "name": "Legacy", "threshold": 5.0}]
    save_rules(rules)
    loaded = load_rules()
    # target_channels NOT auto-added by load_rules
    assert "target_channels" not in loaded[0]
    # ...but normalize_rule fills it in when caller wants it
    assert normalize_rule(loaded[0])["target_channels"] == []
