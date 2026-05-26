"""Tests for engine.rule_templates — the pre-baked AlertRule template
catalog that lowers cold-start friction in the rule editor.

These tests pin both the SHAPE of the catalog (every template has a
unique slug, valid category, valid severity, non-empty metric, etc.)
and the BEHAVIOUR of the public helpers (get_template / list_templates
/ template_to_alert_rule). The catalog itself is asserted at length
because a typo'd template would otherwise only surface when a user
clicked it in the UI — these tests are the early-warning system.
"""
from __future__ import annotations

from dataclasses import fields

import pytest

from engine.alert_engine_v2 import AlertRule
from engine.rule_templates import (
    ALLOWED_CATEGORIES,
    ALLOWED_SEVERITIES,
    RuleTemplate,
    TEMPLATES,
    get_template,
    list_templates,
    template_to_alert_rule,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Catalog integrity — every template loads and is well-formed
# ─────────────────────────────────────────────────────────────────────────────

def test_template_catalog_has_at_least_fifteen_templates() -> None:
    """The library should ship ~15 carefully chosen templates. Anything
    materially smaller defeats the cold-start premise; anything materially
    larger drowns the picker. A minimum of 15 is the lower bound the
    task spec calls for."""
    assert len(TEMPLATES) >= 15


def test_all_templates_load_as_rule_template_instances() -> None:
    """The module-level TEMPLATES constant must be a list of RuleTemplate
    instances — a plain dict would slip past the frozen-dataclass guard
    that protects the catalog from accidental mutation."""
    for tpl in TEMPLATES:
        assert isinstance(tpl, RuleTemplate)


def test_every_template_has_a_unique_slug() -> None:
    """Slugs are the stable id under which the operator references a
    template (URL fragments, telemetry, future bookmarking). A duplicate
    slug would make get_template ambiguous."""
    slugs = [t.slug for t in TEMPLATES]
    assert len(slugs) == len(set(slugs)), (
        f"duplicate slugs found in TEMPLATES: "
        f"{[s for s in slugs if slugs.count(s) > 1]}"
    )


def test_every_template_name_is_unique_and_non_empty() -> None:
    """Names are what the user SEES in the picker selectbox. Duplicates
    create unclickable ambiguity in the UI; empty strings render a
    blank picker row."""
    names = [t.name for t in TEMPLATES]
    for n in names:
        assert n.strip(), "template name must be non-empty"
    assert len(names) == len(set(names)), "duplicate template names"


def test_every_template_metric_is_a_non_empty_string() -> None:
    """The metric is what AlertRule.alert_type gets set to — empty would
    short-circuit every detection-side dispatch on that field."""
    for tpl in TEMPLATES:
        assert isinstance(tpl.metric, str), f"{tpl.slug}: metric not str"
        assert tpl.metric.strip(), f"{tpl.slug}: metric empty"


def test_every_template_severity_is_in_allowed_set() -> None:
    """Severity must be one of CRITICAL / HIGH / MEDIUM / LOW so the
    badge renderer and per-channel routing both recognise it."""
    for tpl in TEMPLATES:
        assert tpl.severity in ALLOWED_SEVERITIES, (
            f"{tpl.slug}: severity {tpl.severity!r} not in {ALLOWED_SEVERITIES}"
        )


def test_every_template_category_is_in_allowed_set() -> None:
    """Categories drive the picker's filter dropdown. An unknown value
    would silently drop the template out of the all-categories view too,
    since list_templates(category=X) returns an empty list for unknowns."""
    for tpl in TEMPLATES:
        assert tpl.category in ALLOWED_CATEGORIES, (
            f"{tpl.slug}: category {tpl.category!r} not in {ALLOWED_CATEGORIES}"
        )


def test_catalog_spans_all_five_categories() -> None:
    """Each of the 5 categories should have at least one template — a
    cold-starting operator should see meaningful coverage across the
    macro / route / port / event / cost axes."""
    seen_categories = {t.category for t in TEMPLATES}
    assert seen_categories == ALLOWED_CATEGORIES, (
        f"missing categories: {ALLOWED_CATEGORIES - seen_categories}; "
        f"unexpected: {seen_categories - ALLOWED_CATEGORIES}"
    )


def test_every_template_description_is_non_empty() -> None:
    """The description renders below the picker when a template is
    selected — it's the only context the operator gets before clicking
    Add. Empty descriptions create a 'mystery rule' experience."""
    for tpl in TEMPLATES:
        assert tpl.description.strip(), f"{tpl.slug}: empty description"


def test_template_is_frozen_dataclass() -> None:
    """Frozen so a template constant cannot be mutated by accident at
    runtime — mutation of a shared template would bleed into every
    subsequent template_to_alert_rule call. This test pins that
    invariant explicitly."""
    tpl = TEMPLATES[0]
    with pytest.raises((AttributeError, Exception)):
        # Frozen dataclasses raise FrozenInstanceError (subclass of
        # AttributeError) on attribute assignment.
        tpl.threshold_pct = 999.9  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
#  Port deficit template (wires into engine.alert_engine_v2.check_port_deficit_alerts)
# ─────────────────────────────────────────────────────────────────────────────

def test_port_deficit_template_present() -> None:
    """The Port Deficit Watch template must be in the catalogue so
    operators can spin up PORT_DEFICIT alerts from the UI picker
    instead of writing the rule by hand."""
    tpl = get_template("port-container-deficit-3d")
    assert tpl is not None
    assert tpl.name == "Port container deficit >3 days"
    assert tpl.category == "port"
    assert tpl.metric == "port_supply_deficit_days"
    assert tpl.severity == "HIGH"
    assert tpl.threshold_pct == 3.0
    # Description must mention BOTH the supply-line provenance and the
    # critical-ladder breakpoint so the operator picking it from the UI
    # understands what it does without reading the code.
    desc_l = tpl.description.lower()
    assert "supply" in desc_l
    assert "-10" in tpl.description  # CRITICAL escalation threshold


# ─────────────────────────────────────────────────────────────────────────────
#  get_template — happy + miss paths
# ─────────────────────────────────────────────────────────────────────────────

def test_get_template_returns_template_for_known_slug() -> None:
    """Sanity-check that the FIRST template in the catalog is round-trip
    addressable via its own slug — without this the catalog would still
    pass shape tests but be effectively un-lookupable."""
    first = TEMPLATES[0]
    got = get_template(first.slug)
    assert got is not None
    assert got.slug == first.slug
    assert got.name == first.name


def test_get_template_returns_none_for_unknown_slug() -> None:
    """Miss path returns None (no exception) so the UI can render a
    fallback without a try/except wrapper around every lookup."""
    assert get_template("definitely-not-a-real-slug-xxx") is None


def test_get_template_returns_none_for_empty_string() -> None:
    """Empty-string slug is the natural state of an unselected
    selectbox — must not blow up."""
    assert get_template("") is None


def test_get_template_returns_none_for_non_string_input() -> None:
    """Defensive: a caller that accidentally passes None or an int
    must not crash (the picker pulls from session state which might
    contain anything)."""
    assert get_template(None) is None  # type: ignore[arg-type]
    assert get_template(42) is None  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
#  list_templates — filtering
# ─────────────────────────────────────────────────────────────────────────────

def test_list_templates_with_none_returns_all() -> None:
    """Default (no filter) returns the full catalog in original order —
    operators want to see everything before narrowing."""
    out = list_templates()
    assert len(out) == len(TEMPLATES)
    assert out == TEMPLATES


def test_list_templates_with_all_sentinel_returns_all() -> None:
    """The UI selectbox prepends '(all)' as the first option; the helper
    must treat that string identically to None so the UI code path stays
    a one-liner."""
    out = list_templates(category="(all)")
    assert out == TEMPLATES


def test_list_templates_filters_by_macro_category() -> None:
    """Asking for one category should only return templates in that
    category — a typo here would leak unrelated rules into the picker."""
    out = list_templates(category="macro")
    assert len(out) >= 1
    assert all(t.category == "macro" for t in out)


def test_list_templates_filters_by_event_category() -> None:
    """Event category includes the zero-cooldown templates (Suez,
    Panama, geopolitical). Verify the filter picks them out."""
    out = list_templates(category="event")
    assert len(out) >= 1
    assert all(t.category == "event" for t in out)
    # Event templates are intentionally cooldown_minutes=0 so back-to-
    # back status updates land. Pin that invariant here.
    assert all(t.cooldown_minutes == 0 for t in out)


def test_list_templates_with_unknown_category_returns_empty() -> None:
    """Unknown categories return [] rather than 'all' — keeps the UI
    honest when a typo'd custom category is passed in."""
    assert list_templates(category="not-a-real-category") == []


def test_list_templates_returns_independent_list() -> None:
    """The returned list must be a copy, not the module-level constant
    — a caller that mutates the result must not corrupt the catalog."""
    out = list_templates()
    out.clear()
    # Catalog must still have all its entries.
    assert len(TEMPLATES) >= 15


# ─────────────────────────────────────────────────────────────────────────────
#  template_to_alert_rule — materialization
# ─────────────────────────────────────────────────────────────────────────────

def test_template_to_alert_rule_produces_valid_alert_rule() -> None:
    """Every required AlertRule field must be set on the materialized
    rule — a missing one would raise TypeError at AlertRule(...) call
    time. We sample one template per category to make sure the helper
    works for the full vocabulary."""
    seen = set()
    for tpl in TEMPLATES:
        if tpl.category in seen:
            continue
        seen.add(tpl.category)
        rule = template_to_alert_rule(tpl)
        assert isinstance(rule, AlertRule)
        assert rule.rule_id == tpl.slug
        assert rule.name == tpl.name
        assert rule.alert_type == tpl.metric
        assert rule.enabled is True
        assert rule.threshold == float(tpl.threshold_pct)
        assert rule.severity == tpl.severity
        assert rule.target_channels == []


def test_template_to_alert_rule_applies_target_channels() -> None:
    """Passing target_channels scopes the rule to specific delivery
    channels by NAME (matching the rule-to-channel routing semantics
    already in AlertRule)."""
    tpl = TEMPLATES[0]
    rule = template_to_alert_rule(
        tpl, target_channels=["primary-email", "ops-slack"]
    )
    assert rule.target_channels == ["primary-email", "ops-slack"]


def test_template_to_alert_rule_target_channels_independent_copy() -> None:
    """Mutating the caller's target_channels list after the helper
    returns must not mutate the AlertRule's internal state — frozen
    dataclasses don't help with list fields, so we copy explicitly."""
    tpl = TEMPLATES[0]
    chans = ["a", "b"]
    rule = template_to_alert_rule(tpl, target_channels=chans)
    chans.append("c")
    assert rule.target_channels == ["a", "b"]


def test_template_to_alert_rule_drops_non_string_channels() -> None:
    """Defensive: bad input (None, int) in target_channels should be
    silently dropped, mirroring normalize_rule's behaviour."""
    tpl = TEMPLATES[0]
    rule = template_to_alert_rule(
        tpl, target_channels=["good", 42, None, "also-good"]  # type: ignore[list-item]
    )
    assert rule.target_channels == ["good", "also-good"]


def test_template_to_alert_rule_cooldown_handling_graceful() -> None:
    """cooldown_minutes is round-tripped onto AlertRule if AlertRule
    exposes that field (parallel commit), and silently no-ops otherwise.
    This test asserts the correct behaviour for whichever schema state
    is currently live — it must NEVER raise."""
    tpl_with_cooldown = next(t for t in TEMPLATES if t.cooldown_minutes > 0)
    rule = template_to_alert_rule(tpl_with_cooldown)
    rule_fields = {f.name for f in fields(AlertRule)}
    if "cooldown_minutes" in rule_fields:
        # Field present → cooldown should be carried through.
        assert getattr(rule, "cooldown_minutes") == tpl_with_cooldown.cooldown_minutes
    else:
        # Field absent → AlertRule must not have a stray attribute and
        # the call must have completed without error (we're here, so it did).
        assert not hasattr(rule, "cooldown_minutes")


def test_template_to_alert_rule_uses_slug_as_rule_id() -> None:
    """rule_id == slug invariant: deduplication, audit logging, and
    save_rules round-trips all key off rule_id; the slug being the
    stable id (NOT a fresh uuid) is the whole point of the catalog."""
    for tpl in TEMPLATES[:3]:
        rule = template_to_alert_rule(tpl)
        assert rule.rule_id == tpl.slug


def test_template_to_alert_rule_default_target_channels_is_empty_list() -> None:
    """No target_channels arg → empty list (the legacy 'every channel
    eligible' semantic that ships with default AlertRule)."""
    tpl = TEMPLATES[0]
    rule = template_to_alert_rule(tpl)
    assert rule.target_channels == []
    # Each call returns its own list (not a shared default-arg list).
    rule.target_channels.append("mutation-test")
    rule2 = template_to_alert_rule(tpl)
    assert rule2.target_channels == []
