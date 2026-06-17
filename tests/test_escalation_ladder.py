"""R034 — probabilistic conflict-escalation ladder per chokepoint.

Pins the defining properties of ``processing.escalation_ladder`` and its
opt-in seam into ``chokepoint_analyzer.compute_chokepoint_risk_score``:

  1. The MODELED transition matrix is a valid Markov matrix (every row a
     probability distribution over LADDER_STATES summing to 1).
  2. The forward state distribution stays a probability distribution at any
     horizon, and horizon 0 is the point mass on the starting state.
  3. expected_risk_score is MONOTONE in the starting rung — a hotter rung
     always prices a higher forward expected severity (the model prices the
     escalation tail). An elevated rung (TENSION) scores at-or-above its own
     deterministic severity at the one-step horizon.
  4. current_state_for maps level / disruption_type onto the ladder rungs as
     documented, accepts a bare string, and never raises.
  5. Every result carries the honest ``"modeled"`` provenance stamp.
  6. The chokepoint seam is OPT-IN and DEFAULT-COMPATIBLE: with the flag off
     the scores are byte-for-byte the deterministic base; with it on the
     ladder can only RAISE a score (max-blend), never lower it, and it raises
     at least one hot chokepoint.
"""
from __future__ import annotations

import pytest

from processing.escalation_ladder import (
    CLOSURE,
    DE_ESCALATING,
    INCIDENT,
    LADDER_STATES,
    PARTIAL,
    TENSION,
    TRANSITION,
    LadderResult,
    current_state_for,
    expected_risk_for_chokepoint,
    expected_risk_score,
    ladder_expected_scores,
    severity,
    state_distribution,
)


# ── 1. Valid Markov matrix ──────────────────────────────────────────────────

def test_transition_is_a_valid_markov_matrix() -> None:
    """Every row is a distribution over LADDER_STATES summing to 1, all ≥ 0."""
    for state in LADDER_STATES:
        row = TRANSITION[state]
        assert set(row.keys()) == set(LADDER_STATES)
        assert all(p >= 0.0 for p in row.values())
        assert sum(row.values()) == pytest.approx(1.0, abs=1e-9)


def test_severity_is_strictly_increasing_along_the_rungs() -> None:
    """The severity vector climbs monotonically DE_ESCALATING → CLOSURE,
    anchored so CLOSURE == 1.0 (a CRITICAL-equivalent realised outcome)."""
    sev = [severity(s) for s in LADDER_STATES]
    assert sev == sorted(sev)
    assert len(set(sev)) == len(sev)        # strictly increasing (no ties)
    assert severity(CLOSURE) == 1.0
    assert 0.0 < severity(DE_ESCALATING) < severity(TENSION)


def test_closure_is_sticky_but_not_absorbing() -> None:
    """CLOSURE holds the most mass on itself (sticky) yet keeps a real path
    back down (closures eventually clear) — never a trap."""
    row = TRANSITION[CLOSURE]
    assert row[CLOSURE] == max(row.values())          # stickiest on itself
    assert row[CLOSURE] < 1.0                          # not absorbing
    assert sum(row[s] for s in LADDER_STATES if s != CLOSURE) > 0.0


# ── 2. Forward state distribution ───────────────────────────────────────────

@pytest.mark.parametrize("start", list(LADDER_STATES))
@pytest.mark.parametrize("horizon", [0, 1, 2, 4, 8])
def test_state_distribution_is_a_probability_distribution(start, horizon) -> None:
    dist = state_distribution(start, horizon=horizon)
    assert set(dist.keys()) == set(LADDER_STATES)
    assert all(p >= -1e-12 for p in dist.values())
    assert sum(dist.values()) == pytest.approx(1.0, abs=1e-9)


def test_horizon_zero_is_the_point_mass_on_the_start() -> None:
    dist = state_distribution(TENSION, horizon=0)
    assert dist[TENSION] == pytest.approx(1.0)
    assert sum(v for s, v in dist.items() if s != TENSION) == pytest.approx(0.0)


def test_negative_horizon_clamps_to_point_mass() -> None:
    """A negative horizon is clamped to 0 rather than raising."""
    assert state_distribution(INCIDENT, horizon=-5) == state_distribution(
        INCIDENT, horizon=0
    )


def test_unknown_start_state_degrades_to_de_escalating() -> None:
    assert state_distribution("NONSENSE", horizon=2) == state_distribution(
        DE_ESCALATING, horizon=2
    )


# ── 3. expected_risk_score — the tail-pricing property ──────────────────────

@pytest.mark.parametrize("horizon", [1, 2, 3, 4])
def test_expected_score_is_monotone_in_the_starting_rung(horizon) -> None:
    """A hotter starting rung always prices a HIGHER forward expected
    severity — the whole point of the ladder. Strictly increasing at these
    finite horizons (the chain has not yet collapsed to its stationary law)."""
    scores = [expected_risk_score(s, horizon=horizon).expected_score
              for s in LADDER_STATES]
    assert scores == sorted(scores)
    # Strict at the hot/cold extremes — CLOSURE must price strictly above calm.
    assert scores[-1] > scores[0]


def test_tension_prices_the_escalation_tail() -> None:
    """An elevated (TENSION) chokepoint's one-step expected score sits AT OR
    ABOVE its own deterministic severity — it prices the escalation tail
    rather than mean-reverting straight back to calm."""
    res = expected_risk_score(TENSION, horizon=1)
    assert res.expected_score >= severity(TENSION)


def test_calm_prices_only_a_small_forward_drift() -> None:
    """A calm (DE_ESCALATING) chokepoint prices a small forward drift —
    modestly above bare calm severity, but far below every elevated rung."""
    calm = expected_risk_score(DE_ESCALATING, horizon=1)
    assert calm.expected_score >= severity(DE_ESCALATING)
    assert calm.expected_score < expected_risk_score(TENSION, horizon=1).expected_score


def test_expected_score_clamped_and_stamped_modeled() -> None:
    for s in LADDER_STATES:
        res = expected_risk_score(s, horizon=3)
        assert isinstance(res, LadderResult)
        assert 0.0 <= res.expected_score <= 1.0
        assert res.provenance == "modeled"
        assert res.note  # a human-readable modeled-provenance note
        assert res.current_state == s


def test_unknown_state_never_raises_and_degrades() -> None:
    res = expected_risk_score("WAT", horizon=2)
    assert res.current_state == DE_ESCALATING


# ── 4. current_state_for mapping ────────────────────────────────────────────

class _CP:
    def __init__(self, level, dtype="NONE"):
        self.current_risk_level = level
        self.current_disruption_type = dtype


@pytest.mark.parametrize("level,dtype,expected", [
    ("CRITICAL", "ACTIVE_CONFLICT", CLOSURE),    # the Suez/Bab-el-Mandeb case
    ("HIGH", "ACTIVE_CONFLICT", PARTIAL),        # conflict forcing rerouting
    ("MODERATE", "ACTIVE_CONFLICT", INCIDENT),   # a discrete conflict event
    ("CRITICAL", "WEATHER", PARTIAL),            # critical non-conflict
    ("HIGH", "NONE", INCIDENT),
    ("MODERATE", "NONE", TENSION),
    ("LOW", "NONE", DE_ESCALATING),
    ("", "NONE", DE_ESCALATING),                 # unknown level → calm floor
])
def test_current_state_for_mapping(level, dtype, expected) -> None:
    assert current_state_for(_CP(level, dtype)) == expected


def test_current_state_for_accepts_bare_string() -> None:
    assert current_state_for("MODERATE") == TENSION
    assert current_state_for("CRITICAL") == PARTIAL  # bare string → no conflict


def test_current_state_for_never_raises_on_garbage() -> None:
    assert current_state_for(None) == DE_ESCALATING
    assert current_state_for(object()) == DE_ESCALATING


# ── 5. Batch over the real registry ─────────────────────────────────────────

def test_ladder_expected_scores_over_real_registry() -> None:
    from processing.chokepoint_analyzer import CHOKEPOINTS

    out = ladder_expected_scores(CHOKEPOINTS, horizon=2)
    assert set(out.keys()) == set(CHOKEPOINTS.keys())
    assert all(isinstance(v, LadderResult) for v in out.values())
    assert all(0.0 <= v.expected_score <= 1.0 for v in out.values())


def test_ladder_expected_scores_empty_registry_is_empty() -> None:
    assert ladder_expected_scores({}, horizon=1) == {}


def test_expected_risk_for_chokepoint_is_map_then_price() -> None:
    cp = _CP("CRITICAL", "ACTIVE_CONFLICT")
    res = expected_risk_for_chokepoint(cp, horizon=1)
    assert res.current_state == CLOSURE
    assert res.expected_score == pytest.approx(
        expected_risk_score(CLOSURE, horizon=1).expected_score
    )


# ── 6. The chokepoint seam: opt-in, default-compatible, never-lowers ────────

def test_seam_off_is_byte_for_byte_the_deterministic_base() -> None:
    """With the flag OFF (the default) the composite scores are identical to
    the original deterministic model — every existing test / the SSI see the
    exact same numbers."""
    from processing.chokepoint_analyzer import compute_chokepoint_risk_score

    base_a = compute_chokepoint_risk_score()
    base_b = compute_chokepoint_risk_score(escalation_ladder=False)
    assert base_a == base_b


def test_seam_on_only_raises_never_lowers() -> None:
    """The ladder blends by max(deterministic, ladder_expected): it can only
    RAISE a chokepoint's score (price the tail), never drop it below the
    deterministic floor — and it raises at least one hot chokepoint."""
    from processing.chokepoint_analyzer import compute_chokepoint_risk_score

    base = compute_chokepoint_risk_score()
    fwd = compute_chokepoint_risk_score(escalation_ladder=True, ladder_horizon=2)

    assert set(fwd.keys()) == set(base.keys())
    # Never below the deterministic floor.
    assert all(fwd[k] >= base[k] - 1e-9 for k in base)
    # At least one chokepoint is lifted by the forward-escalation tail.
    assert any(fwd[k] > base[k] + 1e-9 for k in base)
    # Still clamped to [0, 1].
    assert all(0.0 <= v <= 1.0 for v in fwd.values())
