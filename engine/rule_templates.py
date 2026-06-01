"""rule_templates.py — pre-baked AlertRule template library.

A small curated set of ready-to-use alert rules that lowers cold-start
friction for a new operator who has never authored a rule. Templates are
frozen dataclasses, defined as a module-level constant — no database, no
user editing of templates from the UI (a future commit can ship
user-saved templates if the demand surfaces).

Design notes
------------
* ``RuleTemplate`` is intentionally a NEW shape, not a thin alias for
  ``AlertRule``. The template carries operator-facing metadata
  (``description``, ``category``, human ``name``, ``slug``) that the
  AlertRule dataclass deliberately does not — AlertRule is the runtime
  contract for the alert engine; RuleTemplate is the catalog entry the
  user sees in the picker.
* ``cooldown_minutes`` on the template is round-tripped to AlertRule
  ONLY IF the running AlertRule dataclass exposes that field. The
  feature is being shipped in a parallel commit; this module must work
  cleanly whether or not that field has landed yet. Detection uses
  ``hasattr`` on the dataclass fields tuple to avoid passing an unknown
  kwarg into ``AlertRule(...)``.
* Templates use ``metric`` strings that match the existing UI's metric
  selectbox vocabulary where possible (Freight Rate / Stock Price /
  Port Congestion / Macro Indicator / Sentiment Score) and synthesise
  more specific labels (BDI, Bunker Fuel, etc.) when the standard set
  is too coarse — the UI picker preserves unknown metric values via
  ``metric_options_local`` so a template-instantiated rule with a
  non-standard metric still round-trips through the editor.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Optional

from engine.alert_engine_v2 import AlertRule


# ─────────────────────────────────────────────────────────────────────────────
#  Category + severity vocabularies (mirror the strings the UI displays)
# ─────────────────────────────────────────────────────────────────────────────

ALLOWED_CATEGORIES: frozenset[str] = frozenset(
    {"macro", "route", "port", "event", "cost", "company"}
)
ALLOWED_SEVERITIES: frozenset[str] = frozenset(
    {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
)


# ─────────────────────────────────────────────────────────────────────────────
#  RuleTemplate dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RuleTemplate:
    """A pre-baked alert-rule recipe surfaced in the rule-editor template
    picker. Frozen so a template constant cannot be mutated by accident
    at runtime (mutation of a shared template would bleed into every
    subsequent ``template_to_alert_rule`` call)."""

    slug: str            # stable id, e.g. 'bdi-spike-5pct'
    name: str            # operator-visible label, e.g. 'BDI spike >5%'
    description: str     # one-sentence rationale shown when picked
    category: str        # one of ALLOWED_CATEGORIES
    metric: str          # metric this rule monitors
    threshold_pct: float # numeric threshold (% move, raw value, etc.)
    severity: str        # one of ALLOWED_SEVERITIES
    cooldown_minutes: int = 0


# ─────────────────────────────────────────────────────────────────────────────
#  Template catalog (~15 carefully chosen templates spanning all 6 categories)
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATES: list[RuleTemplate] = [
    # ── macro ──────────────────────────────────────────────────────────────
    RuleTemplate(
        slug="bdi-spike-5pct",
        name="BDI spike >5%",
        description=(
            "Baltic Dry Index single-day move of >5% in either direction. "
            "Reliable leading indicator for dry-bulk shipping demand shifts."
        ),
        category="macro",
        metric="bdi",
        threshold_pct=5.0,
        severity="HIGH",
        cooldown_minutes=360,
    ),
    RuleTemplate(
        slug="bdi-crash-10pct",
        name="BDI crash <-10%",
        description=(
            "Baltic Dry Index drop of >10% in a single session — a sharp "
            "demand-side dislocation that typically precedes container-rate "
            "weakness across multiple lanes."
        ),
        category="macro",
        metric="bdi",
        threshold_pct=10.0,
        severity="CRITICAL",
        cooldown_minutes=360,
    ),
    RuleTemplate(
        slug="bunker-fuel-spike-3pct",
        name="Bunker fuel spike >3%",
        description=(
            "VLSFO bunker fuel price single-day move of >3%. Direct cost "
            "input for every voyage — watch for repricing of forward rates."
        ),
        category="macro",
        metric="bunker_fuel",
        threshold_pct=3.0,
        severity="MEDIUM",
        cooldown_minutes=720,
    ),
    RuleTemplate(
        slug="wti-shock-5pct",
        name="WTI crude shock >5%",
        description=(
            "WTI crude oil single-day move of >5%. Upstream signal for "
            "bunker-fuel pricing and broader transportation cost pressure."
        ),
        category="macro",
        metric="wti_crude",
        threshold_pct=5.0,
        severity="HIGH",
        cooldown_minutes=360,
    ),

    # ── route ──────────────────────────────────────────────────────────────
    RuleTemplate(
        slug="transpacific-delay-2d",
        name="Transpacific delay >2 days",
        description=(
            "Trans-Pacific Eastbound (TPEB) route transit-time slippage of "
            ">2 days vs the rolling 30-day median. Often signals upstream "
            "congestion in CN ports or downstream LA/LB queueing."
        ),
        category="route",
        metric="transpacific_delay_days",
        threshold_pct=2.0,
        severity="HIGH",
        cooldown_minutes=1440,
    ),
    RuleTemplate(
        slug="transatlantic-delay-3d",
        name="Transatlantic delay >3 days",
        description=(
            "North Atlantic Eastbound (NAEB) route transit-time slippage of "
            ">3 days vs baseline. Lower-volume than TPEB so the threshold is "
            "wider — but still material for time-sensitive cargo."
        ),
        category="route",
        metric="transatlantic_delay_days",
        threshold_pct=3.0,
        severity="MEDIUM",
        cooldown_minutes=1440,
    ),
    RuleTemplate(
        slug="asia-europe-disruption",
        name="Asia-Europe route disruption",
        description=(
            "Asia-Europe lane disruption signal — covers Suez transits, Red "
            "Sea reroutings, and major schedule reliability breaks on the "
            "FE-NWE corridor."
        ),
        category="route",
        metric="asia_europe_disruption_score",
        threshold_pct=0.70,
        severity="CRITICAL",
        cooldown_minutes=720,
    ),

    # ── port ───────────────────────────────────────────────────────────────
    RuleTemplate(
        slug="port-congestion-critical",
        name="Any port congestion CRITICAL",
        description=(
            "Any tracked port crossing the 0.90 congestion score threshold. "
            "Expect material dwell-time increases, vessel bunching, and "
            "elevated port fees."
        ),
        category="port",
        metric="port_congestion",
        threshold_pct=0.90,
        severity="CRITICAL",
        cooldown_minutes=360,
    ),
    RuleTemplate(
        slug="la-long-beach-wait-spike",
        name="LA/LB wait time spike >50%",
        description=(
            "Los Angeles + Long Beach combined anchorage wait time jumps "
            ">50% above the rolling baseline. The single most reliable "
            "early warning for trans-Pacific schedule slippage."
        ),
        category="port",
        metric="la_lb_wait_pct",
        threshold_pct=50.0,
        severity="HIGH",
        cooldown_minutes=720,
    ),
    RuleTemplate(
        slug="shanghai-throughput-drop",
        name="Shanghai throughput drop >20%",
        description=(
            "Shanghai port weekly TEU throughput drops >20% vs the prior "
            "4-week mean. Picks up policy shocks (lockdowns, holidays) "
            "and unscheduled berth closures."
        ),
        category="port",
        metric="shanghai_throughput_pct",
        threshold_pct=20.0,
        severity="MEDIUM",
        cooldown_minutes=1440,
    ),
    RuleTemplate(
        slug="port-container-deficit-3d",
        name="Port container deficit >3 days",
        description=(
            "Any tracked port crossing the -3-day container-supply threshold "
            "on the dominant container type. Reads from "
            "processing.port_supply_lines + emits a PORT_DEFICIT alert with "
            "the top exposed tickers inline — operator sees WHO is at risk "
            "without re-running the supply-lines analysis. CRITICAL ladder "
            "at -10 days."
        ),
        category="port",
        metric="port_supply_deficit_days",
        threshold_pct=3.0,
        severity="HIGH",
        cooldown_minutes=720,
    ),
    RuleTemplate(
        slug="world-graph-critical-node-stressed",
        name="Critical world-graph node stressed (betweenness)",
        description=(
            "The most systemically-central node in the unified world graph "
            "(ranked by betweenness centrality over ports, lanes, canals, "
            "companies and commodities) is ALSO under stress — a port carrying "
            "a container-supply deficit or a chokepoint with an elevated risk "
            "score. A disruption at a node that is both a structural chokepoint "
            "AND already strained cascades furthest across the network. Reads "
            "from processing.world_graph + world_graph_metrics; computes via "
            "processing.world_graph_criticality. CRITICAL ladder when the "
            "node's stress reaches 0.60."
        ),
        category="port",
        metric="world_graph_node_stress",
        threshold_pct=30.0,
        severity="HIGH",
        cooldown_minutes=720,
    ),
    RuleTemplate(
        slug="company-port-concentration-45hhi",
        name="Company port-footprint HHI >=0.45",
        description=(
            "Any tracked ticker whose port-footprint HHI crosses 0.45 — the "
            "boundary of the 'Concentrated' band. A disruption at the "
            "dominant port would impact most of that ticker's container "
            "flow. CRITICAL ladder at HHI >= 0.85 (Single-Port Risk). "
            "Reads from processing.port_supply_lines + computes via "
            "processing.company_concentration_alerts; emits a "
            "COMPANY_CONCENTRATION alert with the top-3 ports + their shares "
            "baked into the body."
        ),
        category="company",
        metric="port_footprint_hhi",
        threshold_pct=45.0,
        severity="HIGH",
        cooldown_minutes=720,
    ),
    RuleTemplate(
        slug="route-cargo-flow-anomaly-jsd15",
        name="Route cargo flow anomaly (JSD >= 0.15)",
        description=(
            "Any route whose cargo mix shifts beyond a Jensen-Shannon "
            "divergence of 0.15 from its trailing-14d median — the "
            "'anomalous' band boundary. CRITICAL ladder at JSD >= 0.30 "
            "(the 'shock' band). Reads from processing.cargo_mix_history "
            "(populated daily by the worker) + scores via "
            "processing.cargo_flow_anomaly; emits a CARGO_FLOW_ANOMALY "
            "alert with the top-3 surges + collapses baked into the body. "
            "Silent on fresh installs until the trailing window populates."
        ),
        category="route",
        metric="cargo_flow_jsd",
        threshold_pct=15.0,
        severity="HIGH",
        cooldown_minutes=720,
    ),

    # ── event ──────────────────────────────────────────────────────────────
    RuleTemplate(
        slug="suez-canal-disruption",
        name="Suez Canal status change",
        description=(
            "Any change in Suez Canal transit status — closures, draft "
            "restrictions, transit-fee shifts, or security incidents. "
            "Event-driven, no cooldown."
        ),
        category="event",
        metric="suez_canal_status",
        threshold_pct=1.0,
        severity="CRITICAL",
        cooldown_minutes=0,
    ),
    RuleTemplate(
        slug="panama-canal-restriction",
        name="Panama Canal restriction change",
        description=(
            "Any change in Panama Canal draft, daily transit cap, or "
            "reservation auction pricing. Tracks the drought-driven "
            "capacity rationing regime."
        ),
        category="event",
        metric="panama_canal_status",
        threshold_pct=1.0,
        severity="HIGH",
        cooldown_minutes=0,
    ),
    RuleTemplate(
        slug="geopolitical-shock",
        name="Geopolitical shock in shipping lane",
        description=(
            "Sudden geopolitical event affecting a major shipping lane — "
            "sanctions, military action, port closures by directive. "
            "Event-driven, no cooldown so back-to-back updates land."
        ),
        category="event",
        metric="geopolitical_shock_score",
        threshold_pct=0.75,
        severity="CRITICAL",
        cooldown_minutes=0,
    ),

    # ── cost ───────────────────────────────────────────────────────────────
    RuleTemplate(
        slug="freight-cost-spike-10pct",
        name="FBX freight cost spike >10%",
        description=(
            "Freightos Baltic Index (FBX) global container freight cost "
            "weekly move >10%. Aggregate-level cost shock — material for "
            "anyone with forward booking exposure."
        ),
        category="cost",
        metric="fbx_freight_cost",
        threshold_pct=10.0,
        severity="HIGH",
        cooldown_minutes=720,
    ),
    RuleTemplate(
        slug="container-rate-collapse-15pct",
        name="SCFI container rate <-15%",
        description=(
            "Shanghai Containerized Freight Index (SCFI) weekly collapse "
            ">15%. Spot-market opportunity — favourable for shippers, "
            "headwind for carriers."
        ),
        category="cost",
        metric="scfi_container_rate",
        threshold_pct=15.0,
        severity="MEDIUM",
        cooldown_minutes=1440,
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
#  Public helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_template(slug: str) -> Optional[RuleTemplate]:
    """Look up a template by slug. Returns ``None`` on miss (no exception
    so the UI can render a fallback message without a try/except)."""
    if not isinstance(slug, str) or not slug:
        return None
    for tpl in TEMPLATES:
        if tpl.slug == slug:
            return tpl
    return None


def list_templates(*, category: Optional[str] = None) -> list[RuleTemplate]:
    """Return templates, optionally filtered to a single category.

    ``category=None`` (the default) or the sentinel ``'(all)'`` (matches
    the value the UI selectbox uses for the "all" option) returns every
    template in catalog order. An unknown category returns an empty list
    — keeps the UI from accidentally rendering all templates when the
    user types a typo'd custom category.
    """
    if category is None or category == "(all)":
        return list(TEMPLATES)
    return [t for t in TEMPLATES if t.category == category]


def template_to_alert_rule(
    template: RuleTemplate,
    *,
    target_channels: Optional[list[str]] = None,
) -> AlertRule:
    """Materialize a ``RuleTemplate`` as a runnable ``AlertRule``.

    The template's ``slug`` becomes the rule's ``rule_id``; the human
    ``name`` is carried through. ``metric`` is mapped to AlertRule's
    ``alert_type`` field (AlertRule has no separate ``metric`` slot;
    the alert engine routes by ``alert_type``). ``threshold_pct``
    becomes AlertRule's ``threshold``. ``enabled`` is hard-coded to
    True — a freshly added template-derived rule is on by default.

    ``cooldown_minutes`` is round-tripped ONLY if the running
    ``AlertRule`` dataclass exposes that field — detected at call time
    via the dataclass ``fields()`` tuple, so this module works
    cleanly both before and after the parallel cooldown-feature
    commit lands. Calling ``AlertRule(cooldown_minutes=...)`` against
    a build that lacks the field would raise TypeError; we avoid that
    by only passing the kwarg when the field is present.

    ``target_channels`` defaults to ``[]`` (the legacy "every channel
    is eligible" semantic). Pass a list to scope the rule to specific
    delivery channels by NAME.
    """
    if target_channels is None:
        target_channels = []
    else:
        # Defensive copy + coerce — keep the caller's list independent
        # from the AlertRule's internal state, and reject non-strings
        # the same way ``normalize_rule`` does.
        target_channels = [x for x in target_channels if isinstance(x, str)]

    base_kwargs: dict = {
        "rule_id": template.slug,
        "name": template.name,
        "alert_type": template.metric,
        "enabled": True,
        "threshold": float(template.threshold_pct),
        "severity": template.severity,
        "target_channels": list(target_channels),
    }

    # Round-trip cooldown_minutes ONLY if AlertRule has the field
    # (the field ships in a parallel commit that may or may not be
    # present in the current code). hasattr-on-instance is unreliable
    # for dataclass field detection because defaults make the attr
    # exist on instances already; check the dataclass field list.
    rule_fields = {f.name for f in fields(AlertRule)}
    if "cooldown_minutes" in rule_fields:
        base_kwargs["cooldown_minutes"] = int(template.cooldown_minutes)

    return AlertRule(**base_kwargs)
