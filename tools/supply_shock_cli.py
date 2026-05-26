"""tools/supply_shock_cli.py — CLI runner for supply-shock scenarios.

Drives ``processing.supply_shock_scenarios`` to quantify the company-
level impact of pre-canned shocks (Suez closure, Shanghai 50%, Panama
drought, Red Sea tensions, China quarantine, oil-crisis demand).

Usage::

    # Run every BUILTIN_SCENARIOS, top-10 most-impacted companies each
    python -m tools.supply_shock_cli --all

    # Run one scenario, top-5
    python -m tools.supply_shock_cli --scenario "Shanghai 50% Throughput" --top-n 5

    # Machine-readable JSON output
    python -m tools.supply_shock_cli --all --format json --out shocks.json

    # Markdown for pasting into a ticket / standup deck
    python -m tools.supply_shock_cli --all --format markdown

Exit codes:
  0   every requested scenario ran cleanly
  1   one or more scenarios raised at apply / compare time
  2   bad argument (unknown scenario name, conflicting flags)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


__all__ = ["main", "run_scenarios", "ScenarioRunResult"]


@dataclass
class ScenarioRunResult:
    """One scenario's apply+compare outcome — keeps the report under
    a single object so JSON / text / markdown formatters share state."""

    scenario_name: str
    description: str
    container_type: str
    report: object | None      # ScenarioImpactReport when ok
    ok: bool
    error: str = ""


# ---------------------------------------------------------------------------
# Pure run entry point — testable without subprocess
# ---------------------------------------------------------------------------


def run_scenarios(
    scenario_names: Iterable[str],
    *,
    container_type: str = "40FT_DRY",
    top_n: int = 10,
) -> list[ScenarioRunResult]:
    """Run each named scenario against a shared baseline + return one
    ``ScenarioRunResult`` per name.

    Baseline chains are built ONCE and reused — running 6 scenarios
    costs one upstream join, not six.
    """
    from processing.supply_shock_scenarios import (
        apply_scenario,
        compare_scenario_impact,
        get_scenario,
    )
    from processing.port_supply_lines import build_port_supply_chains

    try:
        baseline = build_port_supply_chains(
            container_type=container_type,
            top_n_companies=max(8, top_n),
        )
    except Exception as exc:  # pragma: no cover - defensive
        # Baseline-building failure means every scenario errs the same
        # way — surface once and stop.
        return [
            ScenarioRunResult(
                scenario_name=name,
                description="",
                container_type=container_type,
                report=None,
                ok=False,
                error=f"baseline build failed: {type(exc).__name__}: {exc}",
            )
            for name in scenario_names
        ]

    out: list[ScenarioRunResult] = []
    for name in scenario_names:
        scen = get_scenario(name)
        if scen is None:
            out.append(ScenarioRunResult(
                scenario_name=name,
                description="",
                container_type=container_type,
                report=None,
                ok=False,
                error=f"unknown scenario name: {name!r}",
            ))
            continue
        try:
            shocked = apply_scenario(
                scen,
                container_type=container_type,
                top_n_companies=max(8, top_n),
                baseline_chains=baseline,
            )
            report = compare_scenario_impact(
                baseline,
                shocked,
                scenario_name=scen.name,
                container_type=container_type,
            )
            out.append(ScenarioRunResult(
                scenario_name=scen.name,
                description=scen.description,
                container_type=container_type,
                report=report,
                ok=True,
            ))
        except Exception as exc:
            out.append(ScenarioRunResult(
                scenario_name=scen.name,
                description=scen.description,
                container_type=container_type,
                report=None,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            ))
    return out


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _format_text(results: list[ScenarioRunResult], top_n: int) -> str:
    lines: list[str] = []
    for r in results:
        lines.append("=" * 72)
        lines.append(f"Scenario: {r.scenario_name}  [{r.container_type}]")
        if r.description:
            lines.append(f"  {r.description}")
        lines.append("-" * 72)
        if not r.ok or r.report is None:
            lines.append(f"  ERROR: {r.error}")
            continue
        report = r.report
        lines.append(f"  {report.summary}")
        lines.append("")
        lines.append(f"  Top-{top_n} company impact (worst delta first):")
        header = (
            f"    {'ticker':<8} {'Δ deficit':>12} "
            f"{'baseline':>12} {'scenario':>12} {'ports':>6}"
        )
        lines.append(header)
        for ci in list(report.company_impacts)[:top_n]:
            lines.append(
                f"    {ci.ticker:<8} "
                f"{ci.total_deficit_delta:+12.4f} "
                f"{ci.baseline_total_deficit:12.4f} "
                f"{ci.scenario_total_deficit:12.4f} "
                f"{ci.n_ports_touched:>6d}"
            )
            if ci.ports_now_critical:
                lines.append(
                    f"        now-critical: {', '.join(ci.ports_now_critical)}"
                )
            if ci.ports_now_recovered:
                lines.append(
                    f"        recovered:    {', '.join(ci.ports_now_recovered)}"
                )
        lines.append("")
    return "\n".join(lines)


def _format_json(results: list[ScenarioRunResult], top_n: int) -> str:
    payload: list[dict] = []
    for r in results:
        if not r.ok or r.report is None:
            payload.append({
                "scenario":       r.scenario_name,
                "description":    r.description,
                "container_type": r.container_type,
                "ok":             False,
                "error":          r.error,
            })
            continue
        report = r.report
        payload.append({
            "scenario":         r.scenario_name,
            "description":      r.description,
            "container_type":   r.container_type,
            "ok":               True,
            "n_ports_baseline": report.n_ports_baseline,
            "n_ports_scenario": report.n_ports_scenario,
            "n_chains_dropped": report.n_chains_dropped,
            "summary":          report.summary,
            "company_impacts": [
                {
                    "ticker":                  ci.ticker,
                    "baseline_total_deficit":  ci.baseline_total_deficit,
                    "scenario_total_deficit":  ci.scenario_total_deficit,
                    "total_deficit_delta":     ci.total_deficit_delta,
                    "ports_now_critical":      ci.ports_now_critical,
                    "ports_now_recovered":     ci.ports_now_recovered,
                    "n_ports_touched":         ci.n_ports_touched,
                }
                for ci in list(report.company_impacts)[:top_n]
            ],
        })
    return json.dumps({
        "ok_count": sum(1 for r in results if r.ok),
        "total":    len(results),
        "top_n":    top_n,
        "results":  payload,
    }, indent=2)


def _format_markdown(results: list[ScenarioRunResult], top_n: int) -> str:
    lines: list[str] = ["# Supply-shock scenario impact"]
    for r in results:
        lines.append("")
        lines.append(f"## {r.scenario_name}")
        if r.description:
            lines.append(f"_{r.description}_")
        if not r.ok or r.report is None:
            lines.append("")
            lines.append(f"**ERROR:** {r.error}")
            continue
        report = r.report
        lines.append("")
        lines.append(report.summary)
        lines.append("")
        lines.append(
            "| ticker | Δ deficit | baseline | scenario | ports |"
        )
        lines.append("|--------|-----------|----------|----------|-------|")
        for ci in list(report.company_impacts)[:top_n]:
            lines.append(
                f"| {ci.ticker} "
                f"| {ci.total_deficit_delta:+.4f} "
                f"| {ci.baseline_total_deficit:.4f} "
                f"| {ci.scenario_total_deficit:.4f} "
                f"| {ci.n_ports_touched} |"
            )
    return "\n".join(lines)


_FORMATTERS = {
    "text":     _format_text,
    "json":     _format_json,
    "markdown": _format_markdown,
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.supply_shock_cli",
        description=(
            "Quantify the company-level impact of pre-canned supply-line "
            "shocks (Suez closure, Shanghai 50%, Panama drought, etc.)."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scenario",
        action="append",
        default=None,
        help=(
            "Run a single named scenario (case-insensitive). "
            "Repeat the flag to run a list."
        ),
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Run every BUILTIN_SCENARIOS in turn.",
    )
    parser.add_argument(
        "--container-type",
        default="40FT_DRY",
        choices=["40FT_DRY", "20FT_DRY", "40FT_HC",
                 "40FT_REEFER", "20FT_TANK"],
        help="Container-type slice of regional supply state (default: 40FT_DRY).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Per-scenario, cap on the most-impacted-companies listing (default 10).",
    )
    parser.add_argument(
        "--format",
        choices=sorted(_FORMATTERS.keys()),
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional file path to write the report to (in addition to stdout).",
    )
    args = parser.parse_args(argv)

    if args.all:
        from processing.supply_shock_scenarios import BUILTIN_SCENARIOS
        names = [s.name for s in BUILTIN_SCENARIOS]
    else:
        names = list(args.scenario or [])
        if not names:
            print("error: --scenario requires at least one name", file=sys.stderr)
            return 2

    if args.top_n <= 0:
        print("error: --top-n must be a positive integer", file=sys.stderr)
        return 2

    results = run_scenarios(
        names,
        container_type=args.container_type,
        top_n=args.top_n,
    )

    formatter = _FORMATTERS[args.format]
    body = formatter(results, args.top_n)
    print(body)

    if args.out:
        try:
            Path(args.out).write_text(body, encoding="utf-8")
        except OSError as exc:
            print(f"error: could not write --out: {exc}", file=sys.stderr)
            return 1

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
