"""Defining-property tests for processing/supply_shock_scenarios.py +
tools/supply_shock_cli.py."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from copy import deepcopy

import pytest

from processing.port_supply_lines import (
    PortExposureChain,
    PortSupplyState,
    build_port_supply_chains,
)
from processing.supply_shock_scenarios import (
    BASE_THROUGHPUT_DAYS,
    BUILTIN_SCENARIOS,
    CompanyScenarioImpact,
    ScenarioImpactReport,
    ShockScenario,
    apply_scenario,
    compare_scenario_impact,
    get_scenario,
)


# ── Module-level fixtures (cache the baseline so the suite stays fast) ─────

@pytest.fixture(scope="module")
def baseline_chains() -> list[PortExposureChain]:
    return build_port_supply_chains()


@pytest.fixture(scope="module")
def first_port_locode(baseline_chains: list[PortExposureChain]) -> str:
    return baseline_chains[0].port.locode


# ── 1. Identity no-op ──────────────────────────────────────────────────────

def test_empty_scenario_is_identity(
    baseline_chains: list[PortExposureChain],
) -> None:
    """Empty multipliers + empty disabled_locodes + empty commodity_shocks
    must return chains identical to the baseline (no-op identity).

    Identity means: same chain count, same port LOCODEs in the same
    order, same supply_deficit_days, same severity labels."""
    scen = ShockScenario(name="noop", description="")
    out = apply_scenario(scen, baseline_chains=baseline_chains)
    assert len(out) == len(baseline_chains)
    for a, b in zip(out, baseline_chains):
        assert a.port.locode == b.port.locode
        assert a.port.supply_deficit_days == b.port.supply_deficit_days
        assert a.port.severity_label == b.port.severity_label


# ── 2. Throughput multiplier monotonicity ──────────────────────────────────

def test_half_throughput_lowers_deficit_for_any_port(
    baseline_chains: list[PortExposureChain],
) -> None:
    """Throughput multiplier of 0.5 on a port must produce a lower
    (more negative) deficit_days for that port — pinned for EVERY
    locode in the registry."""
    base_by_locode = {c.port.locode: c.port.supply_deficit_days
                      for c in baseline_chains}
    for locode in base_by_locode:
        scen = ShockScenario(
            name="half",
            description="",
            port_throughput_multipliers={locode: 0.5},
        )
        out = apply_scenario(scen, baseline_chains=baseline_chains)
        out_def = {c.port.locode: c.port.supply_deficit_days for c in out}
        assert out_def[locode] < base_by_locode[locode], (
            f"{locode}: 0.5× multiplier did not lower deficit"
        )
        # Magnitude: shifted by (0.5 - 1) * BASE_THROUGHPUT_DAYS
        expected = base_by_locode[locode] - 0.5 * BASE_THROUGHPUT_DAYS
        assert out_def[locode] == pytest.approx(expected)


def test_double_throughput_raises_deficit_for_any_port(
    baseline_chains: list[PortExposureChain],
) -> None:
    """Throughput multiplier of 2.0 produces higher (more positive)
    deficit_days — pinned for every LOCODE."""
    base_by_locode = {c.port.locode: c.port.supply_deficit_days
                      for c in baseline_chains}
    for locode in base_by_locode:
        scen = ShockScenario(
            name="double",
            description="",
            port_throughput_multipliers={locode: 2.0},
        )
        out = apply_scenario(scen, baseline_chains=baseline_chains)
        out_def = {c.port.locode: c.port.supply_deficit_days for c in out}
        assert out_def[locode] > base_by_locode[locode], (
            f"{locode}: 2.0× multiplier did not raise deficit"
        )


def test_throughput_multiplier_only_touches_targeted_port(
    baseline_chains: list[PortExposureChain],
    first_port_locode: str,
) -> None:
    """Multiplier on one port must not shift any other port's deficit."""
    scen = ShockScenario(
        name="single",
        description="",
        port_throughput_multipliers={first_port_locode: 0.25},
    )
    out = apply_scenario(scen, baseline_chains=baseline_chains)
    base_def = {c.port.locode: c.port.supply_deficit_days
                for c in baseline_chains}
    out_def = {c.port.locode: c.port.supply_deficit_days for c in out}
    for locode in base_def:
        if locode == first_port_locode:
            continue
        assert out_def[locode] == base_def[locode], (
            f"{locode}: deficit drifted under unrelated multiplier"
        )


# ── 3. Route disabling drops chains ────────────────────────────────────────

def test_route_disabled_excludes_affected_chains(
    baseline_chains: list[PortExposureChain],
) -> None:
    """Disabling a major locode must drop at least one chain.

    CNSHA touches multiple registry routes — when we kill it AND its
    every destination, the surviving chain count must be strictly less
    than the baseline."""
    # Disable both ends of the dominant Asia-Europe lane → forces some
    # chains to lose every touching route.
    scen = ShockScenario(
        name="kill-routes",
        description="",
        route_disabled_locodes=[
            "CNSHA", "USLAX", "NLRTM", "BEANR", "USNYC",
            "AEJEA", "CNNBO", "LKCMB", "GRPIR", "MATNM",
            "USSAV", "BRSAO", "JPYOK", "SGSIN", "USLGB",
            "GBFXT",
        ],
    )
    out = apply_scenario(scen, baseline_chains=baseline_chains)
    assert len(out) < len(baseline_chains), (
        "route-disabling did not drop any chains — engine broken"
    )


# ── 4. Ticker-count invariance under compare ──────────────────────────────

def test_compare_returns_same_ticker_count_with_shock(
    baseline_chains: list[PortExposureChain],
) -> None:
    """``compare_scenario_impact`` must return the same total ticker
    count whether the scenario is applied or not — no companies appear
    or disappear, only their deltas change."""
    # Baseline-vs-baseline (no-op) ticker count
    identity_report = compare_scenario_impact(
        baseline_chains, baseline_chains, scenario_name="identity",
    )
    n_identity = len(identity_report.company_impacts)

    # Apply a real shock and recompare
    scen = ShockScenario(
        name="shanghai-half",
        description="",
        port_throughput_multipliers={"CNSHA": 0.5},
    )
    shocked = apply_scenario(scen, baseline_chains=baseline_chains)
    shocked_report = compare_scenario_impact(
        baseline_chains, shocked, scenario_name=scen.name,
    )

    assert len(shocked_report.company_impacts) == n_identity, (
        "ticker count changed under scenario — engine is dropping "
        "or inventing tickers"
    )


def test_identity_compare_has_zero_deltas(
    baseline_chains: list[PortExposureChain],
) -> None:
    """Baseline vs baseline (no scenario) must produce zero deltas
    for every ticker."""
    report = compare_scenario_impact(baseline_chains, baseline_chains)
    for ci in report.company_impacts:
        assert ci.total_deficit_delta == 0.0
        assert ci.ports_now_critical == []
        assert ci.ports_now_recovered == []


# ── 5. BUILTIN_SCENARIOS catalogue sanity ─────────────────────────────────

def test_at_least_one_builtin_produces_nonzero_impact(
    baseline_chains: list[PortExposureChain],
) -> None:
    """The catalogue must not be all no-ops — at least one
    BUILTIN_SCENARIOS must produce a measurable company-level delta."""
    assert BUILTIN_SCENARIOS, "BUILTIN_SCENARIOS is empty"
    any_nonzero = False
    for scen in BUILTIN_SCENARIOS:
        shocked = apply_scenario(scen, baseline_chains=baseline_chains)
        report = compare_scenario_impact(
            baseline_chains, shocked, scenario_name=scen.name,
        )
        deltas = [
            abs(ci.total_deficit_delta) for ci in report.company_impacts
        ]
        if any(d > 0 for d in deltas):
            any_nonzero = True
            break
    assert any_nonzero, (
        "every BUILTIN_SCENARIOS resolved to no-op — the catalogue's "
        "shock targets miss every live port/commodity"
    )


def test_builtin_scenarios_have_required_fields() -> None:
    """Each builtin must have a non-empty name + description."""
    seen_names = set()
    for s in BUILTIN_SCENARIOS:
        assert s.name and isinstance(s.name, str)
        assert s.description and isinstance(s.description, str)
        assert s.name not in seen_names, f"duplicate scenario name: {s.name}"
        seen_names.add(s.name)


def test_get_scenario_lookup() -> None:
    """``get_scenario`` returns the named builtin (case-insensitive)
    and None for unknowns."""
    sample = BUILTIN_SCENARIOS[0].name
    assert get_scenario(sample) is BUILTIN_SCENARIOS[0]
    assert get_scenario(sample.lower()) is BUILTIN_SCENARIOS[0]
    assert get_scenario("does-not-exist-xyz") is None
    assert get_scenario("") is None


# ── 6. apply_scenario does not mutate baseline ────────────────────────────

def test_apply_scenario_does_not_mutate_baseline(
    baseline_chains: list[PortExposureChain],
) -> None:
    """The shock must operate on a clone — baseline state must survive
    a heavy apply unmodified so callers can reuse the baseline across
    many scenarios (CLI --all does this)."""
    snapshot = deepcopy(baseline_chains)
    scen = ShockScenario(
        name="heavy",
        description="",
        port_throughput_multipliers={
            "CNSHA": 0.5,
            "USLAX": 0.5,
            "NLRTM": 0.5,
        },
        commodity_demand_shocks={"electronics": 1.5},
    )
    _ = apply_scenario(scen, baseline_chains=baseline_chains)
    assert len(baseline_chains) == len(snapshot)
    for a, b in zip(baseline_chains, snapshot):
        assert a.port.supply_deficit_days == b.port.supply_deficit_days
        assert a.port.severity_label == b.port.severity_label


# ── 7. CLI smoke tests ────────────────────────────────────────────────────


def _run_cli(argv: list[str]) -> tuple[int, str]:
    """Invoke ``tools.supply_shock_cli.main`` with captured stdout."""
    from tools.supply_shock_cli import main as cli_main
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(argv)
    return rc, buf.getvalue()


def test_cli_runs_all_without_crashing() -> None:
    """--all must exit 0 across every BUILTIN_SCENARIOS."""
    rc, _ = _run_cli(["--all", "--top-n", "3"])
    assert rc == 0


def test_cli_format_json_emits_valid_json() -> None:
    """--format json must emit something stdlib json.loads can parse,
    with the documented top-level keys."""
    rc, out = _run_cli(["--all", "--format", "json", "--top-n", "3"])
    assert rc == 0
    payload = json.loads(out)
    assert "ok_count" in payload
    assert "total" in payload
    assert "results" in payload
    assert payload["total"] == len(BUILTIN_SCENARIOS)
    for entry in payload["results"]:
        assert "scenario" in entry
        assert "ok" in entry


def test_cli_rejects_unknown_scenario_name() -> None:
    """--scenario with a name that's not in the catalogue must mark
    that result as not-ok + return rc=1."""
    rc, _ = _run_cli([
        "--scenario", "this-is-not-a-real-scenario-name-xyz",
        "--top-n", "3",
    ])
    assert rc == 1


def test_cli_format_markdown_runs() -> None:
    """--format markdown must run cleanly + emit a body that mentions
    each scenario."""
    rc, out = _run_cli([
        "--all", "--format", "markdown", "--top-n", "2",
    ])
    assert rc == 0
    for s in BUILTIN_SCENARIOS:
        assert s.name in out


def test_cli_writes_out_file(tmp_path) -> None:
    """--out must write the same body that's printed to stdout."""
    target = tmp_path / "shocks.json"
    rc, stdout = _run_cli([
        "--all", "--format", "json", "--top-n", "2",
        "--out", str(target),
    ])
    assert rc == 0
    assert target.exists()
    assert target.read_text(encoding="utf-8") == stdout.rstrip("\n")


# ── 8. Output shape sanity ────────────────────────────────────────────────


def test_apply_scenario_returns_dataclass_shape(
    baseline_chains: list[PortExposureChain],
) -> None:
    """The shocked output must still be a list of PortExposureChain
    with PortSupplyState ports — downstream renderers depend on this."""
    scen = ShockScenario(
        name="probe",
        description="",
        port_throughput_multipliers={"CNSHA": 0.7},
    )
    out = apply_scenario(scen, baseline_chains=baseline_chains)
    for c in out:
        assert isinstance(c, PortExposureChain)
        assert isinstance(c.port, PortSupplyState)
        assert isinstance(c.port.supply_deficit_days, float)


def test_compare_impact_report_is_well_typed(
    baseline_chains: list[PortExposureChain],
) -> None:
    """``ScenarioImpactReport.company_impacts`` must be a list of
    ``CompanyScenarioImpact`` ordered worst-first by delta."""
    scen = ShockScenario(
        name="probe",
        description="",
        port_throughput_multipliers={"CNSHA": 0.5},
    )
    shocked = apply_scenario(scen, baseline_chains=baseline_chains)
    report = compare_scenario_impact(baseline_chains, shocked)
    assert isinstance(report, ScenarioImpactReport)
    for ci in report.company_impacts:
        assert isinstance(ci, CompanyScenarioImpact)
    if len(report.company_impacts) >= 2:
        deltas = [ci.total_deficit_delta for ci in report.company_impacts]
        assert deltas == sorted(deltas, reverse=True), (
            "company_impacts not ordered worst-first"
        )
