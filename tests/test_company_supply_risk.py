"""Defining-property tests for processing/company_supply_risk{,_backtest}.py."""
from __future__ import annotations

import pytest

from processing.company_supply_risk import (
    COMPANY_SUPPLY_RISK_SOURCE,
    CompanySupplyRiskScore,
    RISK_BANDS,
    compute_company_supply_risk,
)
from processing.company_supply_risk_backtest import (
    COMPANY_SUPPLY_RISK_BACKTEST_SOURCE,
    RiskRankScorecard,
    RiskStabilityReport,
    validate_risk_score_stability,
)
from processing.port_supply_lines import (
    CompanyExposure,
    CompanyPortFootprint,
    PortExposureChain,
    PortExposureForCompany,
    PortSupplyState,
)


# ---------------------------------------------------------------------------
# Fixture builders — hand-built chains + footprints used by the controlled
# property tests. Kept here so the contract assertions never depend on the
# live registry's quirks.
# ---------------------------------------------------------------------------


def _state(locode: str, deficit: float, label: str) -> PortSupplyState:
    return PortSupplyState(
        locode=locode, name=f"Port {locode}", region="TEST",
        country_iso3="XXX", lat=0.0, lon=0.0,
        supply_deficit_days=deficit, utilization_pct=80.0,
        severity_label=label, container_type="40FT_DRY",
    )


def _chain(state: PortSupplyState, weights: dict[str, float]) -> PortExposureChain:
    exposed = [
        CompanyExposure(ticker=t, exposure_weight=w)
        for t, w in weights.items()
    ]
    return PortExposureChain(
        port=state, exposed_companies=exposed,
        routes_touching=[], top_commodities=[], summary="",
    )


def _footprint(ticker: str, ports: list[tuple[PortSupplyState, float]]) -> CompanyPortFootprint:
    return CompanyPortFootprint(
        ticker=ticker,
        port_exposures=[
            PortExposureForCompany(
                port_locode=s.locode, port_name=s.name, region=s.region,
                supply_deficit_days=s.supply_deficit_days,
                severity_label=s.severity_label,
                lat=s.lat, lon=s.lon, exposure_weight=w,
            )
            for s, w in ports
        ],
        total_exposure=sum(w for _, w in ports),
        deficit_weighted_score=0.0,
        n_deficit_ports=sum(1 for s, _ in ports if s.supply_deficit_days < 0),
        summary="",
    )


# ── 1. Output shape ────────────────────────────────────────────────────────


def test_returns_one_score_per_ticker_with_port_links() -> None:
    scores = compute_company_supply_risk()
    assert isinstance(scores, list)
    assert scores, "expected at least one score from the live registry"
    for s in scores:
        assert isinstance(s, CompanySupplyRiskScore)
        assert isinstance(s.ticker, str) and s.ticker
        assert s.port_count >= 1


def test_tickers_are_unique() -> None:
    scores = compute_company_supply_risk()
    tickers = [s.ticker for s in scores]
    assert len(tickers) == len(set(tickers))


def test_scores_in_unit_range() -> None:
    """``total_risk_score`` is the clipped blend → always within [0, 100]."""
    scores = compute_company_supply_risk()
    for s in scores:
        assert 0.0 <= s.total_risk_score <= 100.0


def test_ordered_by_score_desc() -> None:
    """The list reads worst-first so an operator's daily view leads with
    the most-exposed name."""
    scores = compute_company_supply_risk()
    values = [s.total_risk_score for s in scores]
    assert values == sorted(values, reverse=True)


def test_risk_band_matches_score_range() -> None:
    """The band label must agree with where the score sits in
    ``RISK_BANDS``."""
    scores = compute_company_supply_risk()
    for s in scores:
        # Resolve the expected band locally so the test fails loudly if the
        # band ladder gets edited without bumping the matching helper.
        expected = RISK_BANDS[0][1]
        for lower, label in RISK_BANDS:
            if s.total_risk_score >= lower:
                expected = label
            else:
                break
        assert s.risk_band == expected, (
            f"{s.ticker}: score {s.total_risk_score} → "
            f"band={s.risk_band}, expected {expected}"
        )


def test_top_problem_ports_sorted_desc_with_cap() -> None:
    """``top_problem_ports`` is the top 3 ports by absolute contribution to
    the weighted_deficit_days term, sorted descending."""
    scores = compute_company_supply_risk()
    for s in scores:
        assert len(s.top_problem_ports) <= 3
        # Contribution magnitude = |share × min(deficit, 0)|.
        mags = [
            abs(share * min(deficit, 0.0))
            for _, share, deficit in s.top_problem_ports
        ]
        assert mags == sorted(mags, reverse=True), (
            f"{s.ticker}: top_problem_ports not sorted by contribution"
        )


# ── 2. Hand-built fixtures — controlled deficit vs surplus comparison ─────


def test_critical_deficit_only_scores_higher_than_surplus_only() -> None:
    """A company exposed exclusively to one Critical Deficit port must
    score higher than the same company exposed exclusively to one
    Surplus port."""
    critical_state = _state("CRIT1", deficit=-15.0, label="Critical Deficit")
    surplus_state = _state("SURP1", deficit=8.0, label="Surplus")

    chains_crit = [_chain(critical_state, {"DOOM": 1.0})]
    chains_surp = [_chain(surplus_state, {"DOOM": 1.0})]
    fps_crit = [_footprint("DOOM", [(critical_state, 1.0)])]
    fps_surp = [_footprint("DOOM", [(surplus_state, 1.0)])]

    s_crit = compute_company_supply_risk(chains=chains_crit, footprints=fps_crit)
    s_surp = compute_company_supply_risk(chains=chains_surp, footprints=fps_surp)

    assert len(s_crit) == 1 and len(s_surp) == 1
    assert s_crit[0].total_risk_score > s_surp[0].total_risk_score
    assert s_crit[0].critical_port_count == 1
    assert s_surp[0].critical_port_count == 0


def test_higher_concentration_scores_higher_than_diversified() -> None:
    """All else equal, a company with one heavy port footprint must score
    above one with diversified ports. Hand-built: both companies see the
    same set of mildly-deficit ports, but A's exposure is concentrated
    in one port (share 1.0 there) while B's is split four ways
    (share 0.25 in each). The concentration penalty (HHI on
    share_within_port) plus the weighted-deficit term both penalise A
    more than B."""
    # Four mildly-deficit ports — same deficit so the only difference
    # between A and B is the share distribution.
    ports = [
        _state(f"PORT{i}", deficit=-5.0, label="Deficit")
        for i in range(4)
    ]
    # A is the only ticker at port 0, splits port 1-3 with B.
    # By construction A's share_within_port at port 0 = 1.0,
    # and at port 1-3 = 0.5 (vs B's 0.5).
    chains = [
        _chain(ports[0], {"AAA": 1.0}),
        _chain(ports[1], {"AAA": 0.5, "BBB": 0.5}),
        _chain(ports[2], {"AAA": 0.5, "BBB": 0.5}),
        _chain(ports[3], {"AAA": 0.5, "BBB": 0.5}),
    ]
    fps = [
        # A: dominant at one port + light at three others → concentrated
        _footprint("AAA", [(p, 1.0 if i == 0 else 0.5)
                           for i, p in enumerate(ports)]),
        # B: equal exposure at three ports → diversified
        _footprint("BBB", [(p, 0.5) for p in ports[1:]]),
    ]
    scores = compute_company_supply_risk(chains=chains, footprints=fps)
    by_ticker = {s.ticker: s for s in scores}
    assert "AAA" in by_ticker and "BBB" in by_ticker
    assert by_ticker["AAA"].total_risk_score > by_ticker["BBB"].total_risk_score


def test_empty_footprints_yields_empty_scores() -> None:
    """No port-side links → no scores. Pure defensive contract."""
    out = compute_company_supply_risk(chains=[], footprints=[])
    assert out == []


def test_weighted_deficit_is_negative_or_zero() -> None:
    """``weighted_deficit_days`` is the sum of ``share × min(deficit, 0)``
    — must always be ≤ 0 by construction."""
    scores = compute_company_supply_risk()
    for s in scores:
        assert s.weighted_deficit_days <= 1e-9


# ── 3. Source markers ─────────────────────────────────────────────────────


def test_module_source_marker_present() -> None:
    assert COMPANY_SUPPLY_RISK_SOURCE is not None
    assert getattr(COMPANY_SUPPLY_RISK_SOURCE, "name", "") == "Company Supply Risk"


def test_backtest_source_marker_present() -> None:
    assert COMPANY_SUPPLY_RISK_BACKTEST_SOURCE is not None
    assert getattr(
        COMPANY_SUPPLY_RISK_BACKTEST_SOURCE, "name", "",
    ) == "Company Supply Risk Backtest"


# ── 4. Backtest contracts ─────────────────────────────────────────────────


def test_backtest_returns_stability_report() -> None:
    r = validate_risk_score_stability(n_runs=2)
    assert isinstance(r, RiskStabilityReport)
    assert isinstance(r.scorecards, list) and len(r.scorecards) == 2
    for sc in r.scorecards:
        assert isinstance(sc, RiskRankScorecard)
        assert 0.0 <= sc.jaccard_vs_baseline <= 1.0


def test_backtest_zero_noise_yields_perfect_stability() -> None:
    """``noise=0`` → perturbed runs reproduce the baseline → Jaccard = 1.0
    on every run, mean = 1.0, stable = True."""
    r = validate_risk_score_stability(n_runs=3, noise=0.0, top_n=5)
    assert r.overall_mean_stability == 1.0
    for sc in r.scorecards:
        assert sc.jaccard_vs_baseline == 1.0
        assert set(sc.perturbed_top_n) == set(r.baseline_top_n)
    assert r.stable is True


def test_backtest_heavy_noise_degrades_stability_at_small_top_n() -> None:
    """Heavy noise (±100%) must materially reshuffle the top-2 list — the
    universe has 7 tickers, so the *top-2* set has room to swap.
    Empirically lands ~0.8 mean / ~0.5 min vs the noise=0 ceiling of
    1.0 / 1.0. Pins direction, not a hard floor."""
    light = validate_risk_score_stability(n_runs=8, noise=0.0, top_n=2, seed=2026)
    heavy = validate_risk_score_stability(n_runs=8, noise=1.0, top_n=2, seed=2026)
    assert heavy.overall_min_stability < 1.0
    assert heavy.overall_mean_stability < light.overall_mean_stability


def test_backtest_is_deterministic_across_runs() -> None:
    """Same seed → byte-identical scorecards."""
    a = validate_risk_score_stability(n_runs=3, seed=99)
    b = validate_risk_score_stability(n_runs=3, seed=99)
    a_jaccs = [sc.jaccard_vs_baseline for sc in a.scorecards]
    b_jaccs = [sc.jaccard_vs_baseline for sc in b.scorecards]
    assert a_jaccs == b_jaccs
    assert a.baseline_top_n == b.baseline_top_n


def test_backtest_top_n_caps_baseline_list() -> None:
    r = validate_risk_score_stability(n_runs=2, top_n=3)
    assert len(r.baseline_top_n) <= 3
    for sc in r.scorecards:
        assert len(sc.perturbed_top_n) <= 3


def test_backtest_stable_flag_uses_threshold() -> None:
    """``stable`` flag flips by threshold."""
    r_pass = validate_risk_score_stability(
        n_runs=2, noise=0.0, stability_threshold=0.65,
    )
    assert r_pass.stable is True
    # A 99.9% threshold against any noisy run flips it to False.
    r_fail = validate_risk_score_stability(
        n_runs=4, noise=1.0, top_n=2, stability_threshold=0.999, seed=2026,
    )
    assert r_fail.stable is False
