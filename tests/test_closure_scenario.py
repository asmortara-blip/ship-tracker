"""R258 — probability-weighted closure scenario.

Pins the defining properties of ``processing.closure_scenario``, which fuses the
MODELED escalation-ladder ``P(closure)`` with the conditional
``simulate_chokepoint_closure`` severity into a probability-weighted EXPECTED-cost
view:

  (a) The multiplication IDENTITY: every ``expected_*`` field is EXACTLY
      ``p_closure × conditional_*`` — the whole point of the fusion.
  (b) ``p_closure`` is monotone NON-decreasing in horizon for a cool passage
      (it climbs toward the stationary law) and is strictly HIGHER for a hotter
      starting ladder rung at a fixed horizon (a CLOSURE/PARTIAL chokepoint vs a
      calm DE_ESCALATING one) — the ladder prices the tail.
  (c) A calm passage yields ~0 expected impact (P≈0 kills the conditional
      severity) while a hot one (Suez) yields a high one.
  (d) Provenance is the honest ``"modeled"`` stamp with a non-empty note.
  (e) Never raises on an unknown key — returns an honest zeroed scenario.

Style follows ``tests/test_escalation_ladder.py``.
"""
from __future__ import annotations

import pytest

from processing.chokepoint_analyzer import (
    CHOKEPOINTS,
    simulate_chokepoint_closure,
)
from processing.closure_scenario import (
    ClosureScenario,
    expected_closure_impact,
    expected_closure_impacts,
)
from processing.escalation_ladder import (
    CLOSURE,
    DE_ESCALATING,
    current_state_for,
    state_distribution,
)

# Grounded reference chokepoints (see chokepoint_analyzer.CHOKEPOINTS):
#   suez / bab_el_mandeb → CLOSURE rung  (CRITICAL + ACTIVE_CONFLICT)
#   malacca / dover / gibraltar / lombok_sunda → DE_ESCALATING (calm, LOW+NONE)
_HOT = "suez"
_CALM = "malacca"


# ── (a) The multiplication identity ─────────────────────────────────────────

@pytest.mark.parametrize("key", list(CHOKEPOINTS.keys()))
@pytest.mark.parametrize("horizon", [1, 2, 4, 8])
def test_expected_equals_p_closure_times_conditional_exactly(key, horizon) -> None:
    """Every expected_* field == p_closure × the matching conditional_* field,
    EXACTLY (the defining fusion identity) — at every chokepoint / horizon."""
    s = expected_closure_impact(key, horizon=horizon)
    # rel tolerance: the identity is exact modulo float representation, and the
    # rerouting cost runs to tens of millions, so a bare abs=1e-9 is too tight.
    assert s.expected_rate_impact_pct == pytest.approx(
        s.p_closure * s.conditional_rate_impact_pct, rel=1e-9, abs=1e-9)
    assert s.expected_trade_impact_pct == pytest.approx(
        s.p_closure * s.conditional_trade_impact_pct, rel=1e-9, abs=1e-9)
    assert s.expected_extra_days == pytest.approx(
        s.p_closure * s.conditional_extra_days, rel=1e-9, abs=1e-9)
    assert s.expected_rerouting_cost_usd == pytest.approx(
        s.p_closure * s.conditional_rerouting_cost_usd, rel=1e-9, abs=1e-9)


def test_p_closure_is_the_ladder_closure_mass() -> None:
    """p_closure is exactly ``state_distribution(state, horizon)[CLOSURE]`` for
    the chokepoint's mapped ladder rung — not an independent invention."""
    s = expected_closure_impact(_HOT, horizon=2)
    state = current_state_for(CHOKEPOINTS[_HOT])
    assert s.current_state == state
    assert s.p_closure == pytest.approx(
        state_distribution(state, horizon=2)[CLOSURE], abs=1e-9)


def test_conditional_payload_matches_the_simulation() -> None:
    """The lifted conditional_* numbers and the raw payload come straight from
    ``simulate_chokepoint_closure`` at the scenario's closure_weeks."""
    s = expected_closure_impact(_HOT, horizon=4)
    sim = simulate_chokepoint_closure(_HOT, s.closure_weeks)
    assert s.conditional_impact == sim
    assert s.conditional_rate_impact_pct == pytest.approx(sim["rate_impact_pct"])
    assert s.conditional_trade_impact_pct == pytest.approx(
        sim["global_trade_impact_pct"])
    assert s.conditional_extra_days == pytest.approx(sim["extra_days_if_closed"])
    assert s.conditional_rerouting_cost_usd == pytest.approx(
        sim["rerouting_cost_total_usd"])


# ── (b) p_closure: monotone in horizon (cool) + hotter-rung dominance ───────

def test_p_closure_monotone_nondecreasing_in_horizon_for_a_cool_passage() -> None:
    """For a cool passage the closure mass only accumulates with horizon — it is
    monotone NON-decreasing as the chain rolls toward its stationary law."""
    ps = [expected_closure_impact(_CALM, horizon=h).p_closure
          for h in (0, 1, 2, 4, 8)]
    assert ps == sorted(ps)               # non-decreasing
    assert ps[-1] > ps[0]                 # and it does climb (0 → > 0)


@pytest.mark.parametrize("horizon", [1, 2, 4])
def test_p_closure_strictly_higher_for_a_hotter_starting_rung(horizon) -> None:
    """At a FIXED horizon a hotter starting rung carries strictly more closure
    mass: CLOSURE (suez) > a calm DE_ESCALATING passage (malacca)."""
    hot = expected_closure_impact(_HOT, horizon=horizon)
    calm = expected_closure_impact(_CALM, horizon=horizon)
    assert hot.current_state == CLOSURE
    assert calm.current_state == DE_ESCALATING
    assert hot.p_closure > calm.p_closure


# ── (c) Calm ⇒ ~0 expected impact; hot ⇒ high ───────────────────────────────

def test_calm_passage_yields_near_zero_expected_impact() -> None:
    """A calm passage prices ~0 closure probability at a short horizon, so its
    EXPECTED impact collapses to ~0 even though its conditional severity is
    non-trivial (P × severity, with P≈0)."""
    s = expected_closure_impact(_CALM, horizon=1)
    assert s.p_closure == pytest.approx(0.0, abs=1e-9)
    assert s.conditional_rate_impact_pct > 0.0          # severity IS non-trivial
    assert s.expected_rate_impact_pct == pytest.approx(0.0, abs=1e-9)
    assert s.expected_trade_impact_pct == pytest.approx(0.0, abs=1e-9)


def test_hot_passage_yields_high_expected_impact() -> None:
    """A hot passage (Suez at the CLOSURE rung) carries most of its conditional
    severity through into a substantial EXPECTED impact, and dominates a calm
    passage on every expected axis."""
    hot = expected_closure_impact(_HOT, horizon=1)
    calm = expected_closure_impact(_CALM, horizon=1)
    assert hot.p_closure > 0.5                          # near-certain at h=1
    assert hot.expected_rate_impact_pct > 0.0
    assert hot.expected_rate_impact_pct > calm.expected_rate_impact_pct
    # The expected rate impact is a real fraction of the conditional severity.
    assert hot.expected_rate_impact_pct == pytest.approx(
        hot.p_closure * hot.conditional_rate_impact_pct, abs=1e-9)
    assert hot.expected_rate_impact_pct >= 0.5 * hot.conditional_rate_impact_pct


# ── (d) Provenance is the honest modeled stamp ──────────────────────────────

@pytest.mark.parametrize("key", list(CHOKEPOINTS.keys()))
def test_provenance_modeled_and_note_nonempty(key) -> None:
    s = expected_closure_impact(key, horizon=3)
    assert isinstance(s, ClosureScenario)
    assert s.provenance == "modeled"
    assert s.note                                       # non-empty modeled note
    assert 0.0 <= s.p_closure <= 1.0


# ── (e) Never raises on an unknown key — honest zero ────────────────────────

def test_unknown_key_returns_zeroed_scenario_never_raises() -> None:
    s = expected_closure_impact("not_a_real_chokepoint", horizon=4)
    assert isinstance(s, ClosureScenario)
    assert s.chokepoint_key == "not_a_real_chokepoint"
    assert s.p_closure == 0.0
    assert s.conditional_impact == {}
    assert s.conditional_rate_impact_pct == 0.0
    assert s.expected_rate_impact_pct == 0.0
    assert s.expected_trade_impact_pct == 0.0
    assert s.expected_extra_days == 0.0
    assert s.expected_rerouting_cost_usd == 0.0
    assert s.provenance == "modeled"
    assert s.note


def test_unknown_key_is_zero_at_every_horizon() -> None:
    for h in (0, 1, 4, 12):
        s = expected_closure_impact("garbage", horizon=h)
        assert s.p_closure == 0.0
        assert s.expected_rate_impact_pct == 0.0


# ── batch helper ────────────────────────────────────────────────────────────

def test_batch_over_real_registry_keys_and_shapes() -> None:
    out = expected_closure_impacts(CHOKEPOINTS, horizon=2)
    assert set(out.keys()) == set(CHOKEPOINTS.keys())
    assert all(isinstance(v, ClosureScenario) for v in out.values())
    assert all(0.0 <= v.p_closure <= 1.0 for v in out.values())
    # Identity holds elementwise across the whole batch.
    for v in out.values():
        assert v.expected_rate_impact_pct == pytest.approx(
            v.p_closure * v.conditional_rate_impact_pct, rel=1e-9, abs=1e-9)


def test_batch_default_registry_is_chokepoints() -> None:
    """With no registry the batch defaults to CHOKEPOINTS (lazy import)."""
    out = expected_closure_impacts(horizon=1)
    assert set(out.keys()) == set(CHOKEPOINTS.keys())


def test_batch_empty_registry_is_empty() -> None:
    assert expected_closure_impacts({}, horizon=1) == {}


def test_batch_hot_dominates_calm_on_expected_cost() -> None:
    """In the real registry the hot CLOSURE passage prices a strictly higher
    expected rate impact than a calm one — the ranking the desk wants."""
    out = expected_closure_impacts(CHOKEPOINTS, horizon=1)
    assert out[_HOT].expected_rate_impact_pct > out[_CALM].expected_rate_impact_pct


# ── closure_weeks plumbing ──────────────────────────────────────────────────

def test_closure_weeks_defaults_to_horizon_and_is_overridable() -> None:
    """closure_weeks defaults to the horizon (floored at 1) and an explicit
    override changes only the conditional severity, not p_closure."""
    s_default = expected_closure_impact(_HOT, horizon=4)
    assert s_default.closure_weeks == 4

    s_h0 = expected_closure_impact(_HOT, horizon=0)
    assert s_h0.closure_weeks == 1                      # floored at 1

    s_over = expected_closure_impact(_HOT, horizon=4, closure_weeks=8)
    assert s_over.closure_weeks == 8
    # Same p_closure (same horizon), heavier conditional severity (longer close).
    assert s_over.p_closure == pytest.approx(s_default.p_closure, abs=1e-9)
    assert s_over.conditional_rate_impact_pct > s_default.conditional_rate_impact_pct
