"""Tests for processing.valuation — illustrative equity-valuation models.

Offline, known-answer tests. Every fundamental input is supplied here as an
argument (the module wires no real fundamentals — see docs/DATA_PROVENANCE.md),
so the suite is fully deterministic with no network and no yfinance.

Coverage:
  * DCF closed form vs Gordon-growth perpetuity; zero-growth sanity.
  * r <= g terminal guard (no divide-by-zero, no negative-TV explosion; flagged).
  * scenario ordering invariant: worst <= base <= best per-share.
  * Monte-Carlo determinism (seed), ordered percentiles, skip-count reporting.
  * sensitivity sorted-by-swing; wider range => >= swing; dominant driver found.
  * disruption monotonicity (sev 0.8 <= sev 0.2); severity_from_ssi clamps [0,1].
  * every result carries a non-empty disclaimer + input_provenance.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from processing.valuation import (
    DISCLAIMER,
    ValuationInputs,
    ValuationResult,
    build_disruption_scenarios,
    dcf_valuation,
    disruption_adjusted_inputs,
    illustrative_inputs,
    monte_carlo_valuation,
    scenario_valuation,
    sensitivity_analysis,
    severity_from_ssi,
    summarize_valuation,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────

def _base() -> ValuationInputs:
    """A clean, well-behaved base case (r > g_term)."""
    return ValuationInputs(
        fcf_0=500.0,
        fcf_growth=0.04,
        discount_rate=0.10,
        terminal_growth=0.02,
        shares_outstanding=120.0,
        net_debt=0.0,
    )


# ─── DCF closed form ─────────────────────────────────────────────────────────

def test_dcf_matches_gordon_growth_closed_form() -> None:
    """A constant-growth DCF over a long horizon converges to the Gordon value.

    For constant g and discount r (r > g), the full DCF (explicit FCF + Gordon
    terminal) of an effectively-infinite horizon equals the closed-form growing
    perpetuity from year-1 FCF: EV = fcf_0 * (1 + g) / (r - g).
    """
    fcf_0, g, r = 100.0, 0.03, 0.10
    inp = ValuationInputs(
        fcf_0=fcf_0, fcf_growth=g, discount_rate=r,
        terminal_growth=g, shares_outstanding=10.0, net_debt=0.0,
    )
    res = dcf_valuation(inp, horizon=200)
    gordon_ev = fcf_0 * (1.0 + g) / (r - g)
    assert math.isclose(res.enterprise_value, gordon_ev, rel_tol=1e-3)
    # No clamp needed: r (0.10) safely exceeds g_term (0.03).
    assert res.terminal_growth_clamped is False


def test_dcf_terminal_value_dominates_for_short_horizon() -> None:
    """At a short explicit horizon the discounted terminal value dominates EV.

    A separate closed-form anchor: with only a few explicit years, most of the
    value lives in the Gordon terminal value (the long-run perpetuity), while
    the explicit-period PV is the smaller part. The total EV still equals the
    analytic growing perpetuity regardless of horizon.
    """
    fcf_0, g, r = 200.0, 0.02, 0.09
    inp = ValuationInputs(
        fcf_0=fcf_0, fcf_growth=g, discount_rate=r,
        terminal_growth=g, shares_outstanding=50.0,
    )
    res = dcf_valuation(inp, horizon=5)
    # PV(terminal) should be the lion's share of EV at a short horizon.
    assert res.pv_terminal_value > 0.5 * res.enterprise_value
    assert res.pv_terminal_value > res.pv_explicit_fcf
    # And EV equals the analytic perpetuity, horizon-invariant for constant g.
    gordon_ev = fcf_0 * (1.0 + g) / (r - g)
    assert math.isclose(res.enterprise_value, gordon_ev, rel_tol=2e-3)


def test_dcf_zero_growth_sanity() -> None:
    """Zero growth → a flat perpetuity: EV = fcf_0 / r over a long horizon."""
    fcf_0, r = 80.0, 0.08
    inp = ValuationInputs(
        fcf_0=fcf_0, fcf_growth=0.0, discount_rate=r,
        terminal_growth=0.0, shares_outstanding=20.0,
    )
    res = dcf_valuation(inp, horizon=300)
    assert math.isclose(res.enterprise_value, fcf_0 / r, rel_tol=1e-3)
    # Per-share = (EV - net_debt)/shares.
    assert math.isclose(res.per_share_value, (fcf_0 / r) / 20.0, rel_tol=1e-2)


def test_dcf_net_debt_lowers_equity_value() -> None:
    """Net debt is subtracted from EV before the per-share divide."""
    debt_free = dcf_valuation(_base())
    levered = dcf_valuation(ValuationInputs(
        fcf_0=500.0, fcf_growth=0.04, discount_rate=0.10,
        terminal_growth=0.02, shares_outstanding=120.0, net_debt=1000.0,
    ))
    assert levered.equity_value == pytest.approx(debt_free.equity_value - 1000.0, abs=0.01)
    assert levered.per_share_value < debt_free.per_share_value


# ─── r <= g terminal guard ───────────────────────────────────────────────────

def test_discount_le_terminal_growth_is_clamped_and_flagged() -> None:
    """r <= g_term must not divide-by-zero or explode negative — it is clamped."""
    inp = ValuationInputs(
        fcf_0=100.0, fcf_growth=0.02, discount_rate=0.04,
        terminal_growth=0.06,  # > discount rate
        shares_outstanding=10.0,
    )
    res = dcf_valuation(inp, horizon=5)
    assert res.terminal_growth_clamped is True
    # Finite and positive — not a NaN/inf blow-up, not a negative TV.
    assert math.isfinite(res.per_share_value)
    assert res.per_share_value > 0.0
    assert res.pv_terminal_value > 0.0
    assert any("clamp" in n.lower() for n in res.notes)


def test_discount_equals_terminal_growth_is_clamped() -> None:
    """The exact r == g_term boundary (zero denominator) is also handled."""
    inp = ValuationInputs(
        fcf_0=100.0, fcf_growth=0.03, discount_rate=0.05,
        terminal_growth=0.05, shares_outstanding=10.0,
    )
    res = dcf_valuation(inp, horizon=5)
    assert res.terminal_growth_clamped is True
    assert math.isfinite(res.per_share_value)
    assert res.per_share_value > 0.0


def test_zero_shares_is_guarded() -> None:
    """Zero / negative shares → per-share 0.0 with a note, never a ZeroDivision."""
    for shares in (0.0, -5.0):
        inp = ValuationInputs(
            fcf_0=500.0, fcf_growth=0.04, discount_rate=0.10,
            terminal_growth=0.02, shares_outstanding=shares,
        )
        res = dcf_valuation(inp)
        assert res.per_share_value == 0.0
        assert any("shares" in n.lower() for n in res.notes)


def test_dcf_handles_nan_and_empty_growth() -> None:
    """NaN fcf_0 and an empty growth sequence degrade gracefully (no NaN out)."""
    inp = ValuationInputs(
        fcf_0=float("nan"), fcf_growth=[], discount_rate=0.10,
        terminal_growth=0.02, shares_outstanding=100.0,
    )
    res = dcf_valuation(inp)
    assert math.isfinite(res.per_share_value)


# ─── Scenario ordering ───────────────────────────────────────────────────────

def test_scenario_ordering_worst_le_base_le_best() -> None:
    """worst <= base <= best per-share when scenarios are economically ordered."""
    scenarios = {
        "worst": {"fcf_growth": -0.05, "discount_rate": 0.13},
        "base": {},
        "best": {"fcf_growth": 0.12, "discount_rate": 0.085},
    }
    results = scenario_valuation(_base(), scenarios=scenarios)
    worst = results["worst"].per_share_value
    base = results["base"].per_share_value
    best = results["best"].per_share_value
    assert worst <= base <= best
    # Each result still carries the disclaimer + provenance.
    for r in results.values():
        assert r.disclaimer == DISCLAIMER
        assert r.input_provenance


def test_scenario_margin_override_scales_fcf() -> None:
    """The 'margin' sugar scales fcf_0 (a margin-compression proxy)."""
    base = _base()
    res = scenario_valuation(base, scenarios={
        "compressed": {"margin": 0.5},
        "base": {},
    })
    # Half the FCF => roughly half the equity value (linear in fcf_0).
    assert res["compressed"].per_share_value == pytest.approx(
        res["base"].per_share_value * 0.5, rel=1e-6
    )


def test_scenario_empty_dict_returns_empty() -> None:
    assert scenario_valuation(_base(), scenarios={}) == {}


# ─── Monte-Carlo ─────────────────────────────────────────────────────────────

def _mc_dists() -> dict:
    return {
        "fcf_growth": ("normal", (0.04, 0.02)),
        "discount_rate": ("triangular", (0.08, 0.10, 0.13)),
    }


def test_monte_carlo_is_deterministic_for_same_seed() -> None:
    """Same seed → byte-identical summary."""
    a = monte_carlo_valuation(_base(), distributions=_mc_dists(), n=2000, seed=42)
    b = monte_carlo_valuation(_base(), distributions=_mc_dists(), n=2000, seed=42)
    for key in ("mean", "median", "std", "p5", "p25", "p50", "p75", "p95"):
        assert a[key] == b[key]


def test_monte_carlo_different_seed_changes_draws() -> None:
    """A different seed produces a different draw (mean differs)."""
    a = monte_carlo_valuation(_base(), distributions=_mc_dists(), n=2000, seed=1)
    b = monte_carlo_valuation(_base(), distributions=_mc_dists(), n=2000, seed=2)
    assert a["mean"] != b["mean"]


def test_monte_carlo_percentiles_are_ordered() -> None:
    """p5 <= p25 <= p50 <= p75 <= p95."""
    s = monte_carlo_valuation(_base(), distributions=_mc_dists(), n=5000, seed=7)
    assert s["p5"] <= s["p25"] <= s["p50"] <= s["p75"] <= s["p95"]
    assert s["min"] <= s["p5"]
    assert s["p95"] <= s["max"]
    # n_valid + skipped accounts for every requested draw.
    assert s["n_valid"] + s["skipped"] == s["n_requested"] == 5000


def test_monte_carlo_reports_skip_count_for_degenerate_draws() -> None:
    """A distribution that can sample r <= g_term yields a reported skip count.

    The discount rate is drawn from a uniform that dips below the fixed 4%
    terminal growth, so some draws are non-economic and must be skipped — and
    the skip count must be > 0 (and never silently clamped into the stats).
    """
    dists = {
        # Uniform [0.01, 0.06]; with terminal_growth=0.04 a chunk is <= g.
        "discount_rate": ("uniform", (0.01, 0.06)),
    }
    inp = ValuationInputs(
        fcf_0=500.0, fcf_growth=0.03, discount_rate=0.10,
        terminal_growth=0.04, shares_outstanding=120.0,
    )
    s = monte_carlo_valuation(inp, distributions=dists, n=4000, seed=3)
    assert s["skipped"] > 0
    assert s["n_valid"] > 0
    assert s["n_valid"] + s["skipped"] == 4000
    # No clamped draws leaked in: every valid draw had r > g, so values finite.
    assert math.isfinite(s["mean"])


def test_monte_carlo_all_degenerate_returns_zeros() -> None:
    """If every draw is non-economic, stats are 0.0 and skipped == n."""
    # discount_rate fixed at 0.02, terminal_growth 0.05 => always r < g.
    dists = {"discount_rate": ("fixed", (0.02,))}
    inp = ValuationInputs(
        fcf_0=500.0, fcf_growth=0.03, discount_rate=0.10,
        terminal_growth=0.05, shares_outstanding=120.0,
    )
    s = monte_carlo_valuation(inp, distributions=dists, n=500, seed=0)
    assert s["skipped"] == 500
    assert s["n_valid"] == 0
    assert s["mean"] == 0.0


def test_monte_carlo_carries_disclaimer_and_provenance() -> None:
    s = monte_carlo_valuation(_base(), distributions=_mc_dists(), n=500, seed=0)
    assert s["disclaimer"] == DISCLAIMER
    assert s["input_provenance"]
    assert set(s["percentiles"]) == {"p5", "p25", "p50", "p75", "p95"}


# ─── Sensitivity / tornado ───────────────────────────────────────────────────

def test_sensitivity_returns_rows_sorted_by_swing_desc() -> None:
    """Rows are sorted by swing magnitude, descending (tornado shape)."""
    ranges = {
        "fcf_growth": (0.00, 0.08),
        "discount_rate": (0.085, 0.13),
        "terminal_growth": (0.00, 0.03),
        "net_debt": (0.0, 500.0),
    }
    rows = sensitivity_analysis(_base(), ranges=ranges)
    assert len(rows) == 4
    swings = [r["swing"] for r in rows]
    assert swings == sorted(swings, reverse=True)
    # Each row has the tornado-chart fields.
    for r in rows:
        assert {"input", "low_value", "high_value", "swing"} <= set(r)


def test_sensitivity_wider_range_gives_at_least_as_much_swing() -> None:
    """Widening an input's range cannot shrink its swing (monotone in width)."""
    narrow = sensitivity_analysis(_base(), ranges={"discount_rate": (0.095, 0.105)})
    wide = sensitivity_analysis(_base(), ranges={"discount_rate": (0.07, 0.14)})
    assert wide[0]["swing"] >= narrow[0]["swing"]


def test_sensitivity_identifies_dominant_driver() -> None:
    """The widest-impact input is identifiable as the first (top) row.

    Here a huge net_debt range dwarfs a tiny terminal-growth range, so net_debt
    must be the dominant (first) tornado bar.
    """
    rows = sensitivity_analysis(_base(), ranges={
        "net_debt": (0.0, 5000.0),       # large dollar swing
        "terminal_growth": (0.019, 0.021),  # negligible
    })
    assert rows[0]["input"] == "net_debt"
    assert rows[0]["swing"] > rows[1]["swing"]


def test_sensitivity_empty_returns_empty() -> None:
    assert sensitivity_analysis(_base(), ranges={}) == []


# ─── Disruption linkage ──────────────────────────────────────────────────────

def test_disruption_severity_is_weakly_monotonic_per_share() -> None:
    """Higher severity → lower-or-equal per-share value (monotone non-increasing)."""
    base = illustrative_inputs()
    vals = [
        dcf_valuation(disruption_adjusted_inputs(base, sev)).per_share_value
        for sev in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
    ]
    # Each step is non-increasing.
    for earlier, later in zip(vals, vals[1:]):
        assert later <= earlier + 1e-9
    # And the headline comparison the spec calls out.
    hi = dcf_valuation(disruption_adjusted_inputs(base, 0.8)).per_share_value
    lo = dcf_valuation(disruption_adjusted_inputs(base, 0.2)).per_share_value
    assert hi <= lo


def test_disruption_adjusted_inputs_haircuts_growth_and_lifts_discount() -> None:
    """The documented mapping: growth down, discount up."""
    base = ValuationInputs(fcf_growth=0.06, discount_rate=0.10)
    adj = disruption_adjusted_inputs(base, 0.5)
    # 0.06 - 0.5*0.08 = 0.02 ; 0.10 + 0.5*0.05 = 0.125
    assert adj.fcf_growth == pytest.approx(0.02, abs=1e-9)
    assert adj.discount_rate == pytest.approx(0.125, abs=1e-9)


def test_disruption_adjusted_inputs_handles_sequence_growth() -> None:
    """A per-year growth sequence is haircut element-wise."""
    base = ValuationInputs(fcf_growth=[0.10, 0.06, 0.03], discount_rate=0.10)
    adj = disruption_adjusted_inputs(base, 1.0)  # full -0.08 haircut
    assert adj.fcf_growth == pytest.approx([0.02, -0.02, -0.05], abs=1e-9)


def test_disruption_severity_clamped_to_unit_interval() -> None:
    """Severity outside [0,1] is clamped: 2.0 behaves like 1.0, -1.0 like 0.0."""
    base = illustrative_inputs()
    over = dcf_valuation(disruption_adjusted_inputs(base, 2.0)).per_share_value
    at_one = dcf_valuation(disruption_adjusted_inputs(base, 1.0)).per_share_value
    under = dcf_valuation(disruption_adjusted_inputs(base, -1.0)).per_share_value
    at_zero = dcf_valuation(disruption_adjusted_inputs(base, 0.0)).per_share_value
    assert over == at_one
    assert under == at_zero


def test_build_disruption_scenarios_preserves_ordering() -> None:
    """worst <= base <= best when worst is disruption-driven."""
    base = illustrative_inputs()
    scen = build_disruption_scenarios(base, severity=0.7)
    assert set(scen) == {"worst", "base", "best"}
    results = scenario_valuation(base, scenarios=scen)
    assert (
        results["worst"].per_share_value
        <= results["base"].per_share_value
        <= results["best"].per_share_value
    )


def test_severity_from_ssi_clamps_to_unit_interval() -> None:
    """severity_from_ssi normalises and clamps to [0,1]."""
    assert severity_from_ssi(50.0, ssi_max=100.0) == pytest.approx(0.5)
    assert severity_from_ssi(150.0, ssi_max=100.0) == 1.0     # over-max clamps
    assert severity_from_ssi(-10.0, ssi_max=100.0) == 0.0     # negative clamps
    # SSI already on a 0–1 axis.
    assert severity_from_ssi(0.42, ssi_max=1.0) == pytest.approx(0.42)
    # Degenerate ssi_max guarded.
    assert severity_from_ssi(5.0, ssi_max=0.0) == 0.0


# ─── Provenance + disclaimer on every result ─────────────────────────────────

def test_every_dcf_result_has_disclaimer_and_full_provenance() -> None:
    """Every ValuationResult carries a non-empty disclaimer + complete provenance."""
    res = dcf_valuation(_base())
    assert isinstance(res, ValuationResult)
    assert res.disclaimer and "not investment advice" in res.disclaimer.lower()
    # All six fundamentals are flagged.
    assert set(res.input_provenance) == {
        "fcf_0", "fcf_growth", "discount_rate",
        "terminal_growth", "shares_outstanding", "net_debt",
    }
    # Default provenance is "assumed" — the honest default per the audit.
    assert all(v == "assumed" for v in res.input_provenance.values())


def test_provenance_respects_caller_real_flag_but_backfills_rest() -> None:
    """A caller can flag a field 'real'; unflagged fields stay 'assumed'."""
    inp = ValuationInputs(input_provenance={"fcf_0": "real"})
    assert inp.input_provenance["fcf_0"] == "real"
    assert inp.input_provenance["discount_rate"] == "assumed"
    # And it propagates into the result.
    res = dcf_valuation(inp)
    assert res.input_provenance["fcf_0"] == "real"


def test_summarize_valuation_leads_with_value_and_ends_with_disclaimer() -> None:
    res = dcf_valuation(_base())
    summary = summarize_valuation(res, ticker="ZIM")
    assert summary.startswith("ZIM")
    assert summary.rstrip().endswith("docs/DATA_PROVENANCE.md.")
    assert "assumed" in summary.lower()


def test_illustrative_inputs_are_all_assumed() -> None:
    """The convenience builder always marks every fundamental 'assumed'."""
    inp = illustrative_inputs(fcf_0=900.0)
    assert inp.fcf_0 == 900.0
    assert all(v == "assumed" for v in inp.input_provenance.values())


# ─── Regression: terminal-spread clamp + MC guard + uniform scenario horizon ──
# Bug-hunt 2026-06-01: the Gordon clamp fired only on spread <= 0, so a discount
# rate just above terminal growth (0 < r-g < floor) produced a near-singular but
# FINITE per-share (~1e18) that broke monotonicity; the MC degenerate-draw guard
# had the same threshold gap; and a per-scenario `horizon` override silently
# broke the worst<=base<=best ordering.

def test_dcf_spread_just_below_floor_is_clamped_not_singular() -> None:
    """0 < r - g_term < floor must be clamped to a bounded value (was a
    near-singular finite explosion that slipped past the isfinite guard)."""
    near = ValuationInputs(fcf_0=600.0, fcf_growth=0.03, discount_rate=0.0245,
                           terminal_growth=0.02, shares_outstanding=120.0, net_debt=0.0)
    res = dcf_valuation(near)              # spread = 0.0045 < 0.005 floor
    assert math.isfinite(res.per_share_value)
    assert res.per_share_value < 1e5, "clamp must keep the value bounded"
    at_floor = ValuationInputs(fcf_0=600.0, fcf_growth=0.03, discount_rate=0.025,
                               terminal_growth=0.02, shares_outstanding=120.0, net_debt=0.0)
    # The just-below-floor case clamps to ~the exact-floor case, not millions.
    assert res.per_share_value == pytest.approx(dcf_valuation(at_floor).per_share_value, rel=0.5)
    assert any("floor" in n.lower() for n in res.notes)


def test_disruption_adjusted_dcf_monotone_non_increasing_in_severity() -> None:
    """Higher severity must never raise per-share value (fuzzed across bases)."""
    rng = np.random.default_rng(7)
    for _ in range(400):
        base = ValuationInputs(
            fcf_0=float(rng.uniform(50, 2000)),
            fcf_growth=float(rng.uniform(-0.05, 0.10)),
            discount_rate=float(rng.uniform(0.04, 0.15)),
            terminal_growth=float(rng.uniform(0.0, 0.10)),
            shares_outstanding=float(rng.uniform(20, 300)),
            net_debt=float(rng.uniform(-500, 2000)),
        )
        prev = None
        for sev in np.linspace(0.0, 1.0, 21):
            ps = dcf_valuation(disruption_adjusted_inputs(base, float(sev))).per_share_value
            assert math.isfinite(ps) and abs(ps) < 1e7
            if prev is not None:
                assert ps <= prev + 1e-6, f"non-monotone at severity {sev}"
            prev = ps


def test_monte_carlo_skips_near_singular_draws_and_bounds_tail() -> None:
    """Draws within the spread floor of g_term are skipped (counted), so the
    tail is bounded rather than inflated by clamped giants."""
    base = ValuationInputs(fcf_0=600.0, fcf_growth=0.03, discount_rate=0.07,
                           terminal_growth=0.04, shares_outstanding=120.0, net_debt=0.0)
    mc = monte_carlo_valuation(
        base, distributions={"discount_rate": ("uniform", (0.0405, 0.10))},
        n=20000, seed=0,
    )
    assert mc["skipped"] > 0, "near-singular draws should be skipped + counted"
    assert mc["max"] <= 15.0 * max(mc["median"], 1e-9), "tail must be bounded"
    assert math.isfinite(mc["mean"]) and math.isfinite(mc["max"])


def test_scenario_per_scenario_horizon_does_not_break_ordering() -> None:
    """A stray per-scenario 'horizon' override must be ignored (uniform horizon),
    preserving worst <= base <= best."""
    base = _base()
    scen = scenario_valuation(base, scenarios={
        "worst": {"fcf_growth": 0.01, "horizon": 1},
        "base": {"fcf_growth": 0.06},
        "best": {"fcf_growth": 0.12, "horizon": 1},
    })
    worst = scen["worst"].per_share_value
    mid = scen["base"].per_share_value
    best = scen["best"].per_share_value
    assert worst <= mid <= best, f"ordering broke: {worst} / {mid} / {best}"
