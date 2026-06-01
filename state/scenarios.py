"""state/scenarios.py — typed what-if scenarios + overlay mixin.

Lets any tab answer "what would this number look like if X happened?" against
a shared, named set of canonical scenarios — without each tab inventing its
own shock-application logic.

Design
------
A :class:`Scenario` is a frozen bundle of named :class:`ScenarioShock` records.
Each shock specifies:

  - ``target`` — a *target key* using the ``<namespace>:<id>.<field>`` form
    (e.g., ``route:asia_europe.rate``, ``ticker:ZIM.return``,
    ``commodity:wti.spot``, ``macro:bdi.level``). Wildcards are supported in the
    ``<id>`` slot via ``*`` (e.g., ``route:*.rate`` = every route's rate).

  - ``multiplier`` — applied multiplicatively (1.20 = +20%, 0.75 = −25%).
  - ``addend``     — applied additively *after* the multiplier. Useful for
    delays expressed in days, or sentiment shocks that compose with a level.

A :class:`Scenario` is *active* when its id is stored in
``SessionState.scenario_overlays["_active"]``. Tabs read the active scenario
via :func:`active_scenario` and apply it to their values via
:func:`overlay_value` (per-value) or :func:`overlay_multiplier` /
:func:`overlay_addend` (when only the factor is needed).

Why a separate module
---------------------
``state/session.py`` already has a ``scenario_overlays: dict[str, float]``
field — the *storage* slot. This module is the *schema* + *catalog* +
*application logic*. Tabs depend on this module, not on the storage shape.

Pure module — no streamlit *required* (it's imported lazily inside the few
helpers that read/write session state, so tests can exercise the schema and
the apply logic without a Streamlit runtime).
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Iterable, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScenarioShock:
    """One shock inside a :class:`Scenario`.

    ``target`` follows ``<namespace>:<id>.<field>``. The ``<id>`` slot may be
    ``*`` to match every id in that namespace+field — e.g., ``route:*.rate``
    raises every route's rate.

    The shock is applied as::

        new_value = base_value * multiplier + addend
    """
    target: str
    multiplier: float = 1.0
    addend: float = 0.0
    description: str = ""


@dataclass(frozen=True)
class Scenario:
    """A named what-if bundling one or more :class:`ScenarioShock` records."""
    id: str
    name: str
    summary: str
    category: str                     # "Geopolitical" | "Weather" | "Macro" | "Operational" | "Demand"
    shocks: tuple[ScenarioShock, ...] = field(default_factory=tuple)


# ─────────────────────────────────────────────────────────────────────────────
# Target-key matching
# ─────────────────────────────────────────────────────────────────────────────

def _split_target(target: str) -> tuple[str, str, str]:
    """Parse ``<namespace>:<id>.<field>`` → (namespace, id, field).

    Raises ``ValueError`` on malformed input. The leading namespace and
    trailing field are required; the id may be ``*``.
    """
    if ":" not in target:
        raise ValueError(f"scenario target missing namespace: {target!r}")
    namespace, rest = target.split(":", 1)
    if "." not in rest:
        raise ValueError(f"scenario target missing field: {target!r}")
    ident, fld = rest.rsplit(".", 1)
    if not namespace or not ident or not fld:
        raise ValueError(f"scenario target has empty segment: {target!r}")
    return namespace, ident, fld


def _target_matches(shock_target: str, query_target: str) -> bool:
    """Return True if a shock's target spec covers a tab's query target.

    Wildcards are allowed in the shock's ``<id>`` slot only — namespace and
    field must match exactly. The shock can't be wildcard in its namespace
    or field (that would be too coarse to be useful and error-prone).
    """
    try:
        s_ns, s_id, s_fld = _split_target(shock_target)
        q_ns, q_id, q_fld = _split_target(query_target)
    except ValueError:
        return False
    if s_ns != q_ns or s_fld != q_fld:
        return False
    return fnmatch.fnmatchcase(q_id, s_id)


# ─────────────────────────────────────────────────────────────────────────────
# Apply logic — pure functions on (value, scenario) pairs
# ─────────────────────────────────────────────────────────────────────────────

def _aggregate_shocks(
    scenario: Optional[Scenario],
    target: str,
) -> tuple[float, float]:
    """Combine every matching shock in ``scenario`` into a single
    (multiplier, addend) pair. Multipliers compound multiplicatively; addends
    sum. Returns ``(1.0, 0.0)`` (identity) when ``scenario`` is None.
    """
    if scenario is None:
        return 1.0, 0.0
    mult = 1.0
    add = 0.0
    for shock in scenario.shocks:
        if _target_matches(shock.target, target):
            mult *= float(shock.multiplier)
            add += float(shock.addend)
    return mult, add


def overlay_multiplier(target: str, scenario: Optional[Scenario] = None) -> float:
    """Return the compounded multiplier for a target under ``scenario``.

    Returns 1.0 when ``scenario`` is None or no shock matches — call sites
    can multiply the result through and remain correct in both cases.
    """
    if scenario is None:
        scenario = active_scenario()
    mult, _ = _aggregate_shocks(scenario, target)
    return mult


def overlay_addend(target: str, scenario: Optional[Scenario] = None) -> float:
    """Return the summed additive offset for a target under ``scenario``.

    Returns 0.0 when ``scenario`` is None or no shock matches.
    """
    if scenario is None:
        scenario = active_scenario()
    _, add = _aggregate_shocks(scenario, target)
    return add


def overlay_value(
    target: str,
    base_value: float,
    scenario: Optional[Scenario] = None,
) -> float:
    """Apply the active (or provided) scenario to a single value.

    Formula: ``base_value * multiplier + addend``. Both default to identity
    when no scenario or no matching shock, so call sites can use this
    unconditionally and the no-overlay code path is unchanged.
    """
    if scenario is None:
        scenario = active_scenario()
    mult, add = _aggregate_shocks(scenario, target)
    return base_value * mult + add


def overlay_iterable(
    target_template: str,
    items: Iterable[tuple[str, float]],
    scenario: Optional[Scenario] = None,
) -> dict[str, float]:
    """Bulk apply a scenario across a set of (id, value) pairs.

    ``target_template`` should contain ``{id}`` as a placeholder — e.g.,
    ``"route:{id}.rate"``. Each item's id is substituted, then each value
    is overlaid. Useful when applying a wildcard shock across many routes
    or tickers in one pass.
    """
    if scenario is None:
        scenario = active_scenario()
    out: dict[str, float] = {}
    for ident, val in items:
        target = target_template.format(id=ident)
        out[ident] = overlay_value(target, val, scenario)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Session-state binding — read/write the active scenario
# ─────────────────────────────────────────────────────────────────────────────

_ACTIVE_KEY = "_active"
"""Reserved key inside ``SessionState.scenario_overlays`` storing the id of
the currently-active scenario. Using ``_active`` (with the leading
underscore) keeps it from colliding with legacy free-form overlay multipliers
that older tabs may have written into the same dict."""


def active_scenario() -> Optional[Scenario]:
    """Return the currently-active scenario, or ``None`` if none is set.

    Read path:
      1. Look up ``SessionState.scenario_overlays["_active"]`` for a scenario id.
      2. Resolve it through :data:`SCENARIO_CATALOG`.
      3. Return ``None`` on any miss — making "no scenario" the default.

    Pure-python tests can stub :data:`SCENARIO_CATALOG` or call this with no
    session available; the function tolerates a missing Streamlit runtime.
    """
    try:
        from state.session import get_session

        state = get_session()
    except Exception:
        return None
    scenario_id = state.scenario_overlays.get(_ACTIVE_KEY)
    if not isinstance(scenario_id, str) or not scenario_id:
        return None
    return SCENARIO_CATALOG.get(scenario_id)


def set_active_scenario(scenario_id: Optional[str]) -> None:
    """Set or clear the active scenario.

    Passing ``None`` (or any unknown id) clears the active scenario by
    removing the ``_active`` key. The catalog is consulted so callers can't
    activate a scenario that doesn't exist.
    """
    try:
        from state.session import get_session

        state = get_session()
    except Exception:
        return
    if scenario_id is None or scenario_id not in SCENARIO_CATALOG:
        state.scenario_overlays.pop(_ACTIVE_KEY, None)
        return
    state.scenario_overlays[_ACTIVE_KEY] = scenario_id


# ─────────────────────────────────────────────────────────────────────────────
# Canonical scenario catalog
# ─────────────────────────────────────────────────────────────────────────────

SCENARIO_CATALOG: dict[str, Scenario] = {
    # ── Geopolitical ────────────────────────────────────────────────────────
    "suez_closure": Scenario(
        id="suez_closure",
        name="Suez Canal Closure (90 days)",
        summary=(
            "Hard closure of the Suez Canal forces Asia-Europe traffic around "
            "the Cape of Good Hope, adding ~10 days transit and pushing rates "
            "+35% on affected lanes. Tanker rates surge on supply-chain bottleneck."
        ),
        category="Geopolitical",
        shocks=(
            ScenarioShock("route:asia_europe.rate", multiplier=1.35,
                          description="Asia-Europe spot +35%"),
            ScenarioShock("route:med_hub_to_asia.rate", multiplier=1.30,
                          description="Europe-Asia spot +30%"),
            ScenarioShock("route:asia_europe.transit_days", addend=10.0,
                          description="+10 day reroute via Cape"),
            ScenarioShock("commodity:wti.spot", multiplier=1.12,
                          description="Crude +12% on supply-chain risk"),
            ScenarioShock("macro:bdi.level", multiplier=1.20,
                          description="BDI +20% on tonne-mile demand"),
            ScenarioShock("ticker:ZIM.return", multiplier=1.18,
                          description="ZIM revenue uplift"),
        ),
    ),

    "us_china_tariff_25": Scenario(
        id="us_china_tariff_25",
        name="US-China Tariff Escalation (+25%)",
        summary=(
            "Additional 25-percentage-point tariff on Chinese-origin goods "
            "triggers six-month front-loading wave on Trans-Pacific lanes "
            "(+18% rate) before demand cliff drives them down (~-12% on the "
            "rebound). Asia-Europe spillover is mild."
        ),
        category="Geopolitical",
        shocks=(
            ScenarioShock("route:transpacific_eb.rate", multiplier=1.18,
                          description="TP-EB +18% on pull-forward demand"),
            ScenarioShock("route:transpacific_wb.rate", multiplier=0.92,
                          description="TP-WB -8% on backhaul slack"),
            ScenarioShock("ticker:MATX.return", multiplier=1.10,
                          description="Matson's Pacific exposure benefits"),
            ScenarioShock("ticker:ZIM.return", multiplier=1.08,
                          description="ZIM TP capacity gains"),
        ),
    ),

    "houthi_escalation": Scenario(
        id="houthi_escalation",
        name="Houthi Strikes Escalate",
        summary=(
            "Sustained Houthi missile activity in the Red Sea forces most "
            "operators to suspend Bab el-Mandeb transits indefinitely. "
            "Asia-Europe goes 100% Cape-routed; insurance premiums and "
            "rates jump on every related lane."
        ),
        category="Geopolitical",
        shocks=(
            ScenarioShock("route:asia_europe.rate", multiplier=1.45),
            ScenarioShock("route:med_hub_to_asia.rate", multiplier=1.40),
            ScenarioShock("route:middle_east_to_europe.rate", multiplier=1.55),
            ScenarioShock("route:middle_east_to_asia.rate", multiplier=1.25),
            ScenarioShock("route:asia_europe.transit_days", addend=10.0),
            ScenarioShock("commodity:wti.spot", multiplier=1.18,
                          description="Crude +18% on tanker rerouting"),
        ),
    ),

    # ── Macro ───────────────────────────────────────────────────────────────
    "oil_spike_30": Scenario(
        id="oil_spike_30",
        name="Oil Price Spike (+30%)",
        summary=(
            "Crude jumps 30% on supply shock. Bunker cost surges, compressing "
            "carrier margins. Slow-steaming reduces effective capacity, "
            "pushing rates modestly higher."
        ),
        category="Macro",
        shocks=(
            ScenarioShock("commodity:wti.spot", multiplier=1.30),
            ScenarioShock("commodity:brent.spot", multiplier=1.30),
            ScenarioShock("route:*.rate", multiplier=1.06,
                          description="All rates +6% on bunker cost pass-through"),
            ScenarioShock("ticker:*.return", multiplier=0.94,
                          description="Carrier earnings -6% on bunker cost"),
        ),
    ),

    "demand_recession": Scenario(
        id="demand_recession",
        name="Global Demand Recession",
        summary=(
            "Synchronized OECD slowdown pulls containerized demand down 8% "
            "year-over-year. Rates fall hardest on consumer-goods lanes; "
            "blank sailings ramp; carrier returns turn negative."
        ),
        category="Demand",
        shocks=(
            ScenarioShock("route:transpacific_eb.rate", multiplier=0.78),
            ScenarioShock("route:asia_europe.rate", multiplier=0.80),
            ScenarioShock("route:*.rate", multiplier=0.92),  # everything else mild
            ScenarioShock("macro:bdi.level", multiplier=0.75),
            ScenarioShock("ticker:*.return", multiplier=0.82),
        ),
    ),

    "panama_drought": Scenario(
        id="panama_drought",
        name="Panama Canal Drought",
        summary=(
            "Lake Gatun water levels force daily-transit cuts at the Panama "
            "Canal. Trans-Pacific Atlantic and US Gulf↔Asia lanes see longer "
            "transits and rate uplift; oil tankers reroute via Magellan."
        ),
        category="Weather",
        shocks=(
            ScenarioShock("route:transpacific_eb.rate", multiplier=1.08),
            ScenarioShock("route:longbeach_to_asia.rate", multiplier=1.05),
            ScenarioShock("route:china_south_america.rate", multiplier=1.12),
            ScenarioShock("route:*.transit_days", addend=2.0,
                          description="Mild +2d on all lanes touching Panama"),
        ),
    ),
}


def list_scenarios() -> list[Scenario]:
    """Return the catalog as a list sorted by id — convenient for UI selectors."""
    return [SCENARIO_CATALOG[k] for k in sorted(SCENARIO_CATALOG)]


def get_scenario(scenario_id: str) -> Optional[Scenario]:
    """Look up a scenario by id; returns None if not found."""
    return SCENARIO_CATALOG.get(scenario_id)


__all__ = [
    "Scenario",
    "ScenarioShock",
    "SCENARIO_CATALOG",
    "active_scenario",
    "set_active_scenario",
    "overlay_value",
    "overlay_multiplier",
    "overlay_addend",
    "overlay_iterable",
    "list_scenarios",
    "get_scenario",
]
