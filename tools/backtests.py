"""tools/backtests.py — run every platform backtest in one command.

Consolidates the 8 per-module backtest validators shipped under
``processing/*_backtest.py`` + ``engine/*_backtest*.py`` +
``processing/ssi_component_validation.py`` +
``engine/schi_component_validation.py`` into a single CLI that runs
all of them with default arguments and prints a consolidated
operator-facing report.

Useful for:

  * **CI gating** — fail the build if any roll-up flag flips wrong
    (``--strict``)
  * **Operator sanity checks** — one shell command answers "is every
    analytical module still producing a credible signal?"
  * **Documentation regeneration** — `--format markdown` emits a table
    that can be pasted into a docs page

Usage::

    python -m tools.backtests                   # human-readable text
    python -m tools.backtests --format json     # one JSON blob
    python -m tools.backtests --format markdown # markdown table
    python -m tools.backtests --strict          # exit 1 on any flag failure
    python -m tools.backtests --drift-strict     # + exit 1 on a HEADLINE-metric
                                                 #   regression past tolerance (R027)

The CLI does NOT take per-module flags — by design, this is the
\"run them all\" path. For tuning a single validator use its Python
entry point directly.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Result wrapper — uniform shape across the heterogeneous validator outputs
# ---------------------------------------------------------------------------


@dataclass
class BacktestResult:
    """One validator's run, normalised into a uniform shape."""

    name: str
    headline_label: str    # e.g. "Monotonic ladder", "Best component"
    headline_value: str    # e.g. "yes", "chokepoint (60.5%)"
    healthy: bool          # roll-up flag — True if the validator's
                           # primary calibration / monotonicity check passed
    summary: str           # the validator's one-line plain-language summary
    raw_fields: dict[str, Any] = field(default_factory=dict)
    # Optional per-class scorecard rows surfaced by --verbose.
    # Each row: ``{"label": str, "metric_name": str, "value": str}``.
    scorecard_rows: list[dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-validator adapters — each returns a BacktestResult
# ---------------------------------------------------------------------------


def _run_ssi_components() -> BacktestResult:
    from processing.ssi_component_validation import validate_ssi_components
    r = validate_ssi_components()
    best = next(sc for sc in r.scorecards if sc.component == r.best_component)
    worst = next(sc for sc in r.scorecards if sc.component == r.worst_component)
    # Healthy when the best component is meaningfully above random.
    healthy = best.sign_agreement_rate >= 0.55
    scorecard_rows = [
        {"label": sc.component,
         "metric_name": "sign-agreement",
         "value": f"{sc.sign_agreement_rate * 100:.1f}%"}
        for sc in sorted(r.scorecards,
                         key=lambda s: -s.sign_agreement_rate)
    ]
    return BacktestResult(
        name="SSI Component Predictiveness",
        headline_label="Best component",
        headline_value=f"{r.best_component} ({best.sign_agreement_rate * 100:.1f}% sign-agreement)",
        healthy=healthy,
        summary=r.summary,
        raw_fields={
            "n_observations":   r.n_observations,
            "best_component":   r.best_component,
            "worst_component":  r.worst_component,
            "best_rate":        round(best.sign_agreement_rate, 4),
            "worst_rate":       round(worst.sign_agreement_rate, 4),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_schi_components() -> BacktestResult:
    from engine.schi_component_validation import validate_schi_components
    r = validate_schi_components()
    best = next(sc for sc in r.scorecards if sc.component == r.best_component)
    worst = next(sc for sc in r.scorecards if sc.component == r.worst_component)
    healthy = best.sign_agreement_rate >= 0.55
    scorecard_rows = [
        {"label": sc.component,
         "metric_name": "sign-agreement",
         "value": f"{sc.sign_agreement_rate * 100:.1f}%"}
        for sc in sorted(r.scorecards,
                         key=lambda s: -s.sign_agreement_rate)
    ]
    return BacktestResult(
        name="SCHI Dimension Predictiveness",
        headline_label="Best dimension",
        headline_value=f"{r.best_component} ({best.sign_agreement_rate * 100:.1f}% sign-agreement)",
        healthy=healthy,
        summary=r.summary,
        raw_fields={
            "n_observations":   r.n_observations,
            "best_dimension":   r.best_component,
            "worst_dimension":  r.worst_component,
            "best_rate":        round(best.sign_agreement_rate, 4),
            "worst_rate":       round(worst.sign_agreement_rate, 4),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_disruption_forecast() -> BacktestResult:
    from processing.disruption_forecast_backtest import backtest_disruption_forecast
    r = backtest_disruption_forecast()
    # Healthy when 30d sign-agreement is materially above random.
    healthy = r.mean_sign_agreement_30d >= 0.55
    scorecard_rows = [
        {"label": sc.route_id,
         "metric_name": "30d MAE",
         "value": f"{sc.mae_30d:.3f}"}
        for sc in sorted(r.scorecards, key=lambda s: s.mae_30d)
    ]
    return BacktestResult(
        name="Disruption Forecast Accuracy",
        headline_label="30d sign-agreement",
        headline_value=f"{r.mean_sign_agreement_30d * 100:.1f}% (MAE {r.mean_mae_30d:.3f})",
        healthy=healthy,
        summary=r.summary,
        raw_fields={
            "n_observations":      r.n_observations,
            "mean_mae_7d":         round(r.mean_mae_7d, 4),
            "mean_mae_30d":        round(r.mean_mae_30d, 4),
            "mean_sa_7d":          round(r.mean_sign_agreement_7d, 4),
            "mean_sa_30d":         round(r.mean_sign_agreement_30d, 4),
            "best_route":          r.best_route,
            "worst_route":         r.worst_route,
        },
        scorecard_rows=scorecard_rows,
    )


def _run_momentum_ranker() -> BacktestResult:
    from engine.momentum_ranker_backtest import backtest_momentum_signals
    r = backtest_momentum_signals()
    scorecard_rows = [
        {"label": sc.signal,
         "metric_name": "mean fwd return",
         "value": f"{sc.mean_forward_return * 100:+.2f}%"}
        for sc in r.scorecards
    ]
    return BacktestResult(
        name="Momentum Ranker Ladder",
        headline_label="Monotonic ladder",
        headline_value=("yes" if r.monotonic_by_signal else "no"),
        healthy=bool(r.monotonic_by_signal),
        summary=r.summary,
        raw_fields={
            "n_observations":          r.n_observations,
            "monotonic_by_signal":     bool(r.monotonic_by_signal),
            "spread_strong_vs_weak":   round(r.spread_strong_vs_weak, 4),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_freight_volatility() -> BacktestResult:
    from processing.freight_volatility_backtest import backtest_freight_volatility
    r = backtest_freight_volatility()
    healthy = r.momentum_works and r.mean_reversion_works
    if healthy:
        headline = "both"
    elif r.momentum_works or r.mean_reversion_works:
        headline = "mixed"
    else:
        headline = "neither"
    scorecard_rows = [
        {"label": sc.regime,
         "metric_name": "mean fwd return",
         "value": f"{sc.mean_forward_return * 100:+.2f}%"}
        for sc in r.regimes
    ] + [
        {"label": sc.signal,
         "metric_name": "mean fwd return",
         "value": f"{sc.mean_forward_return * 100:+.2f}%"}
        for sc in r.mean_reversion
    ]
    return BacktestResult(
        name="Freight Volatility Classifier",
        headline_label="Momentum + reversion",
        headline_value=headline,
        healthy=healthy,
        summary=r.summary,
        raw_fields={
            "n_observations":         r.n_observations,
            "momentum_works":         bool(r.momentum_works),
            "mean_reversion_works":   bool(r.mean_reversion_works),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_leading_indicators() -> BacktestResult:
    from processing.leading_indicators_backtest import backtest_leading_indicators
    r = backtest_leading_indicators()
    scorecard_rows = [
        {"label": sc.signal,
         "metric_name": "mean fwd demand",
         "value": f"{sc.mean_forward_demand_pct * 100:+.2f}%"}
        for sc in r.scorecards
    ]
    return BacktestResult(
        name="Leading Indicators Calibration",
        headline_label="Calibrated",
        headline_value=("yes" if r.signals_calibrated else "no"),
        healthy=bool(r.signals_calibrated),
        summary=r.summary,
        raw_fields={
            "n_observations":              r.n_observations,
            "signals_calibrated":          bool(r.signals_calibrated),
            "spread_bullish_vs_bearish":   round(r.spread_bullish_vs_bearish, 4),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_news_sentiment() -> BacktestResult:
    from processing.news_sentiment_backtest import backtest_news_sentiment
    r = backtest_news_sentiment()
    scorecard_rows = [
        {"label": sc.sentiment,
         "metric_name": "mean fwd rate move",
         "value": f"{sc.mean_forward_rate_move_pct * 100:+.2f}%"}
        for sc in r.scorecards
    ]
    return BacktestResult(
        name="News Sentiment Calibration",
        headline_label="Calibrated",
        headline_value=("yes" if r.sentiment_calibrated else "no"),
        healthy=bool(r.sentiment_calibrated),
        summary=r.summary,
        raw_fields={
            "n_observations":              r.n_observations,
            "sentiment_calibrated":        bool(r.sentiment_calibrated),
            "spread_bullish_vs_bearish":   round(r.spread_bullish_vs_bearish, 4),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_vulnerability_scorer() -> BacktestResult:
    from processing.vulnerability_scorer_backtest import backtest_vulnerability_scorer
    r = backtest_vulnerability_scorer()
    scorecard_rows = [
        {"label": sc.label,
         "metric_name": "disrupt rate",
         "value": f"{sc.realized_disruption_rate * 100:.1f}%"}
        for sc in r.scorecards
    ]
    return BacktestResult(
        name="Vulnerability Scorer Monotonicity",
        headline_label="Monotonic ladder",
        headline_value=("yes" if r.monotonic_by_label else "no"),
        healthy=bool(r.monotonic_by_label),
        summary=r.summary,
        raw_fields={
            "n_observations":            r.n_observations,
            "monotonic_by_label":        bool(r.monotonic_by_label),
            "spread_critical_vs_low":    round(r.spread_critical_vs_low, 4),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_eta_predictor() -> BacktestResult:
    from processing.eta_predictor_backtest import backtest_eta_predictor
    r = backtest_eta_predictor()
    # Healthy when the monotonicity ladder holds + the MAE is plausible
    # (under 3 days — well above quality=1.0's collapse, well under
    # quality=0.0's noise floor)
    healthy = bool(r.monotonic_by_label) and r.delay_mae < 3.0
    scorecard_rows = [
        {"label": sc.label,
         "metric_name": "mean realized delay",
         "value": f"{sc.mean_realized_delay_days:.1f}d"}
        for sc in r.label_scorecards
    ]
    return BacktestResult(
        name="ETA Predictor Accuracy",
        headline_label="Monotonic + low MAE",
        headline_value=(
            f"yes (MAE {r.delay_mae:.2f}d, +{r.spread_severe_vs_low:.1f}d spread)"
            if r.monotonic_by_label
            else f"no (MAE {r.delay_mae:.2f}d)"
        ),
        healthy=healthy,
        summary=r.summary,
        raw_fields={
            "n_observations":           r.n_observations,
            "delay_mae":                round(r.delay_mae, 4),
            "delay_sign_agreement":     round(r.delay_sign_agreement, 4),
            "monotonic_by_label":       bool(r.monotonic_by_label),
            "spread_severe_vs_low":     round(r.spread_severe_vs_low, 4),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_port_supply_lines() -> BacktestResult:
    from processing.port_supply_lines_backtest import validate_supply_chain_stability
    r = validate_supply_chain_stability()
    scorecard_rows = [
        {"label": sc.locode,
         "metric_name": "mean stability",
         "value": f"{sc.mean_stability * 100:.1f}%"}
        for sc in r.scorecards
    ]
    return BacktestResult(
        name="Port Supply Lines Stability",
        headline_label="Mean rank stability",
        headline_value=(
            f"{r.overall_mean_stability * 100:.1f}% "
            f"(worst port {r.overall_min_stability * 100:.1f}%)"
        ),
        healthy=bool(r.stable),
        summary=r.summary,
        raw_fields={
            "n_runs":                  r.n_runs,
            "noise":                   r.noise,
            "top_k":                   r.top_k,
            "overall_mean_stability":  round(r.overall_mean_stability, 4),
            "overall_min_stability":   round(r.overall_min_stability, 4),
            "stable":                  bool(r.stable),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_cargo_flow_jsd_stability() -> BacktestResult:
    from processing.analytics_backtests import validate_cargo_flow_jsd_stability
    r = validate_cargo_flow_jsd_stability()
    scorecard_rows = [
        {"label": f"run{pr['run_index']}",
         "metric_name": "identity / swap / disjoint",
         "value": (
             f"{'OK' if pr['identity_ok'] else 'X'} / "
             f"{'OK' if pr['swap_ok'] else 'X'} / "
             f"{'OK' if pr['disjoint_ok'] else 'X'}"
         )}
        for pr in r["per_run"]
    ]
    return BacktestResult(
        name="Cargo-Flow JSD Stability",
        headline_label="JSD checks pass rate",
        headline_value=(
            f"{r['pass_rate'] * 100:.1f}% ({r['passes']}/{r['n_runs']})"
        ),
        healthy=bool(r["passed"]),
        summary=r["summary"],
        raw_fields={
            "n_runs":         r["n_runs"],
            "passes":         r["passes"],
            "pass_rate":      r["pass_rate"],
            "pass_threshold": r["pass_threshold"],
            "passed":         bool(r["passed"]),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_capacity_demand_persistence() -> BacktestResult:
    from processing.analytics_backtests import (
        validate_capacity_demand_persistence,
    )
    r = validate_capacity_demand_persistence()
    scorecard_rows = [
        {"label": f"run{pr['run_index']}",
         "metric_name": "surplus / balanced classification",
         "value": (
             f"{pr['surplus_direction']} {'OK' if pr['surplus_alert'] else 'X'} / "
             f"{pr['balanced_direction']} {'OK' if not pr['balanced_alert'] else 'X'}"
         )}
        for pr in r["per_run"]
    ]
    return BacktestResult(
        name="Capacity-Demand Persistence",
        headline_label="Persistence checks pass rate",
        headline_value=(
            f"{r['pass_rate'] * 100:.1f}% ({r['passes']}/{r['n_runs']})"
        ),
        healthy=bool(r["passed"]),
        summary=r["summary"],
        raw_fields={
            "n_runs":         r["n_runs"],
            "passes":         r["passes"],
            "pass_rate":      r["pass_rate"],
            "pass_threshold": r["pass_threshold"],
            "passed":         bool(r["passed"]),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_spillover_graph_recall() -> BacktestResult:
    from processing.analytics_backtests import validate_spillover_graph_recall
    r = validate_spillover_graph_recall()
    scorecard_rows = [
        {"label": f"run{pr['run_index']}",
         "metric_name": "edge recovered (support, lift)",
         "value": (
             f"{'OK' if pr['passed'] else 'X'} "
             f"(lift={pr['lift']})"
         )}
        for pr in r["per_run"]
    ]
    return BacktestResult(
        name="Spillover Graph Recall",
        headline_label="Edge recovery pass rate",
        headline_value=(
            f"{r['pass_rate'] * 100:.1f}% ({r['passes']}/{r['n_runs']})"
        ),
        healthy=bool(r["passed"]),
        summary=r["summary"],
        raw_fields={
            "n_runs":         r["n_runs"],
            "passes":         r["passes"],
            "pass_rate":      r["pass_rate"],
            "pass_threshold": r["pass_threshold"],
            "passed":         bool(r["passed"]),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_graph_centrality_dominance() -> BacktestResult:
    from processing.analytics_backtests import validate_graph_centrality_dominance
    r = validate_graph_centrality_dominance()
    scorecard_rows = [
        {"label": f"run{pr['run_index']}",
         "metric_name": "hub @ strict-max betweenness + fragments",
         "value": (
             f"{'OK' if pr['passed'] else 'X'} "
             f"(hub={pr['hub']}, btw={pr['hub_betweenness']}, "
             f"comps={pr['n_components_after_hub_removal']})"
         )}
        for pr in r["per_run"]
    ]
    return BacktestResult(
        name="Graph Centrality Dominance",
        headline_label="Hub dominance pass rate",
        headline_value=(
            f"{r['pass_rate'] * 100:.1f}% ({r['passes']}/{r['n_runs']})"
        ),
        healthy=bool(r["passed"]),
        summary=r["summary"],
        raw_fields={
            "n_runs":         r["n_runs"],
            "passes":         r["passes"],
            "pass_rate":      r["pass_rate"],
            "pass_threshold": r["pass_threshold"],
            "passed":         bool(r["passed"]),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_snapshot_diff_anomaly() -> BacktestResult:
    from processing.snapshot_diff_anomaly_backtest import (
        validate_anomaly_recovery,
    )
    r = validate_anomaly_recovery()
    scorecard_rows = [
        {"label": f"run{pr['run_index']}",
         "metric_name": "recovery / shock-recall / quiet-precision",
         "value": (
             f"{pr['recovery_rate'] * 100:.0f}% / "
             f"{pr['shock_recall'] * 100:.0f}% / "
             f"{pr['quiet_precision'] * 100:.0f}%"
         )}
        for pr in r["per_run"]
    ]
    return BacktestResult(
        name="Snapshot Diff Anomaly Recovery",
        headline_label="Mean recovery rate",
        headline_value=(
            f"{r['mean_recovery_rate'] * 100:.1f}% "
            f"(worst run {r['min_recovery_rate'] * 100:.1f}%)"
        ),
        healthy=bool(r["passed"]),
        summary=r["summary"],
        raw_fields={
            "n_runs":               r["n_runs"],
            "noise":                r["noise"],
            "shock_multiplier":     r["shock_multiplier"],
            "pass_threshold":       r["pass_threshold"],
            "mean_recovery_rate":   r["mean_recovery_rate"],
            "min_recovery_rate":    r["min_recovery_rate"],
            "mean_shock_recall":    r["mean_shock_recall"],
            "mean_quiet_precision": r["mean_quiet_precision"],
            "passed":               bool(r["passed"]),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_historical_event_replay() -> BacktestResult:
    from processing.historical_event_replay import (
        replay_all_events, summarize_replay,
    )
    results = replay_all_events()
    s = summarize_replay(results)
    # One scorecard row per event so --verbose shows the full replay grid.
    scorecard_rows = [
        {"label": r.event_id or f"event{i}",
         "metric_name": "replay outcome",
         "value": (
             "PASS" if r.passed
             else f"FAIL (missing={len(r.missing_kinds)} "
                  f"unexpected={len(r.unexpected_kinds)})"
         )}
        for i, r in enumerate(results)
    ]
    return BacktestResult(
        name="Historical Event Replay",
        headline_label="Replay pass rate",
        headline_value=(
            f"{s['pass_rate'] * 100:.1f}% "
            f"({s['passed']}/{s['total']} events)"
        ),
        healthy=bool(s["pass_rate"] >= 0.7) if s["total"] > 0 else True,
        summary=(
            f"Replayed {s['total']} registered event(s). "
            f"miss_rate={s['miss_rate'] * 100:.0f}% "
            f"false_positive_rate={s['false_positive_rate'] * 100:.0f}%"
        ),
        raw_fields={
            "total":               s["total"],
            "passed":              s["passed"],
            "failed":              s["failed"],
            "pass_rate":           s["pass_rate"],
            "miss_rate":           s["miss_rate"],
            "false_positive_rate": s["false_positive_rate"],
            "top_missing_kinds":   s["top_missing_kinds"],
        },
        scorecard_rows=scorecard_rows,
    )


def _run_ssi_port_correlation() -> BacktestResult:
    from processing.ssi_port_correlation_backtest import (
        validate_leading_indicator_recovery,
    )
    r = validate_leading_indicator_recovery()
    # One row per synthetic run; --verbose surfaces detected_lag + r.
    scorecard_rows = [
        {"label": f"run{pr['run_index']}",
         "metric_name": f"detected_lag (truth {pr['true_lag']}d)",
         "value": f"{pr['detected_lag']}d (r={pr['best_r']})"}
        for pr in r["per_run"]
    ]
    return BacktestResult(
        name="SSI Lag-Correlation Recovery",
        headline_label="Lag recovery rate",
        headline_value=(
            f"{r['recovery_rate'] * 100:.1f}% "
            f"(mean |Δlag| {r['mean_abs_lag_error']:.1f}d)"
        ),
        healthy=bool(r["passed"]),
        summary=r["summary"],
        raw_fields={
            "n_runs":              r["n_runs"],
            "noise":               r["noise"],
            "true_lag_days":       r["true_lag_days"],
            "tolerance_days":      r["tolerance_days"],
            "recoveries":          r["recoveries"],
            "recovery_rate":       round(r["recovery_rate"], 4),
            "mean_abs_lag_error":  round(r["mean_abs_lag_error"], 4),
            "mean_best_r":         round(r["mean_best_r"], 4),
            "passed":              bool(r["passed"]),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_company_supply_risk() -> BacktestResult:
    from processing.company_supply_risk_backtest import validate_risk_score_stability
    r = validate_risk_score_stability()
    # One row per perturbation run; label by run index so --verbose
    # surfaces per-run Jaccard.
    scorecard_rows = [
        {"label": f"run{sc.run_index}",
         "metric_name": "top-N jaccard",
         "value": f"{sc.jaccard_vs_baseline * 100:.1f}%"}
        for sc in r.scorecards
    ]
    return BacktestResult(
        name="Company Supply Risk Stability",
        headline_label="Mean top-N stability",
        headline_value=(
            f"{r.overall_mean_stability * 100:.1f}% "
            f"(worst run {r.overall_min_stability * 100:.1f}%)"
        ),
        healthy=bool(r.stable),
        summary=r.summary,
        raw_fields={
            "n_runs":                  r.n_runs,
            "noise":                   r.noise,
            "top_n":                   r.top_n,
            "overall_mean_stability":  round(r.overall_mean_stability, 4),
            "overall_min_stability":   round(r.overall_min_stability, 4),
            "stable":                  bool(r.stable),
        },
        scorecard_rows=scorecard_rows,
    )


def _run_var_coverage() -> BacktestResult:
    """VaR coverage (Kupiec POF) on REAL cached returns — the first risk number
    checked against realized P&L. ``healthy`` only goes False on a genuine
    miscalibration (the model is statistically REJECTED on real data); an empty
    cache reports 'not evaluated' and stays vacuously healthy."""
    from processing.var_coverage_backtest import run_var_coverage_backtest
    r = run_var_coverage_backtest()

    if r.basis != "real":
        return BacktestResult(
            name="VaR Coverage (Kupiec POF)",
            headline_label="status",
            headline_value="not evaluated (no cached price history)",
            healthy=True,                       # vacuous — nothing to reject
            summary=r.summary,
            raw_fields={"basis": r.basis, "n_observations": r.n_observations},
            # Carry one honest status row so the every-validator-has-rows
            # invariant holds even with no real cache (e.g. a fresh CI checkout,
            # which has no cache/stocks/*.parquet) — empty rows would fail it.
            scorecard_rows=[{"label": "status", "metric_name": "basis",
                             "value": r.basis}],
        )

    # Conditional coverage RAISES the bar: a VaR that passes the Kupiec count
    # but CLUSTERS its breaches (Christoffersen-rejected) is not healthy.
    healthy = bool(r.well_calibrated) and not bool(r.breaches_clustered)
    cc_label = (
        "clustered" if r.breaches_clustered else
        ("independent" if r.independence_assessable else "timing n/a")
    )
    return BacktestResult(
        name="VaR Coverage (Kupiec POF)",
        headline_label=f"{r.confidence*100:.0f}% VaR breach rate",
        headline_value=(
            f"{r.breach_rate*100:.1f}% vs {r.nominal_rate*100:.1f}% nominal "
            f"({'calibrated' if healthy else 'REJECTED'}; breaches {cc_label})"
        ),
        healthy=healthy,
        summary=r.summary,
        raw_fields={
            "n_observations": r.n_observations,
            "n_breaches":     r.n_breaches,
            "breach_rate":    round(r.breach_rate, 4),
            "nominal_rate":   round(r.nominal_rate, 4),
            "kupiec_lr":      round(r.kupiec_lr, 4),
            "kupiec_pvalue":  round(r.kupiec_pvalue, 4),
            "rejected":       bool(r.rejected),
            "lr_independence": (round(r.lr_independence, 4)
                                if r.lr_independence is not None else None),
            "pvalue_independence": (round(r.pvalue_independence, 4)
                                    if r.pvalue_independence is not None else None),
            "independence_assessable": bool(r.independence_assessable),
            "breaches_clustered":      bool(r.breaches_clustered),
            "lr_conditional_coverage": (round(r.lr_conditional_coverage, 4)
                                        if r.lr_conditional_coverage is not None else None),
            "method":         r.method,
            "window":         r.window,
        },
        scorecard_rows=[
            {"label": t, "metric_name": "in book", "value": "yes"}
            for t in r.tickers
        ],
    )


def _run_es_coverage() -> BacktestResult:
    """Expected-Shortfall coverage (Acerbi-Szekely Test 2) on REAL cached returns
    — validates the tail-MEAN the desk sizes against, not just the VaR quantile.
    Reported next to the Kupiec result (it inherits the deployed VaR's calibration
    error). ``healthy`` goes False only on a genuine miscalibration (the ES is
    statistically REJECTED at 95% or 99% on real data); an empty cache reports
    'not evaluated' and stays vacuously healthy."""
    from processing.es_coverage_backtest import run_es_coverage_backtest
    r99 = run_es_coverage_backtest(confidence=0.99)
    r95 = run_es_coverage_backtest(confidence=0.95)

    if r99.basis != "real":
        return BacktestResult(
            name="ES Coverage (Acerbi-Szekely)",
            headline_label="status",
            headline_value="not evaluated (no cached price history)",
            healthy=True,                       # vacuous — nothing to reject
            summary=r99.summary,
            raw_fields={"basis": r99.basis, "n_observations": r99.n_observations},
            scorecard_rows=[{"label": "status", "metric_name": "basis",
                             "value": r99.basis}],
        )

    # Rejected at EITHER confidence means the ES is mis-scaled where it matters.
    healthy = bool(r99.well_scaled) and (r95.basis != "real" or bool(r95.well_scaled))
    return BacktestResult(
        name="ES Coverage (Acerbi-Szekely)",
        headline_label="99% ES tail-mean (Z2)",
        headline_value=(
            f"Z2={r99.z2:.2f} vs crit {r99.z2_critical:.2f} "
            f"({'well-scaled' if r99.well_scaled else 'UNDERESTIMATES tail'})"
        ),
        healthy=healthy,
        summary=r99.summary,
        raw_fields={
            "n_observations":   r99.n_observations,
            "nu":               r99.nu,
            "window":           r99.window,
            "z2_99":            round(r99.z2, 4),
            "z2_critical_99":   round(r99.z2_critical, 4),
            "n_breaches_99":    r99.n_breaches,
            "rejected_99":      bool(r99.rejected),
            "mean_tail_loss_99":   round(r99.mean_tail_loss, 6),
            "mean_es_forecast_99": round(r99.mean_es_forecast, 6),
            "z2_95":            (round(r95.z2, 4) if r95.basis == "real" else None),
            "z2_critical_95":   (round(r95.z2_critical, 4) if r95.basis == "real" else None),
            "n_breaches_95":    r95.n_breaches,
            "rejected_95":      bool(r95.rejected) if r95.basis == "real" else None,
        },
        scorecard_rows=[
            {"label": t, "metric_name": "in book", "value": "yes"}
            for t in r99.tickers
        ],
    )


# Canonical list — order is the display order.
ADAPTERS: list[Callable[[], BacktestResult]] = [
    _run_ssi_components,
    _run_schi_components,
    _run_disruption_forecast,
    _run_momentum_ranker,
    _run_freight_volatility,
    _run_leading_indicators,
    _run_news_sentiment,
    _run_vulnerability_scorer,
    _run_eta_predictor,
    _run_port_supply_lines,
    _run_company_supply_risk,
    _run_ssi_port_correlation,
    _run_historical_event_replay,
    _run_snapshot_diff_anomaly,
    _run_cargo_flow_jsd_stability,
    _run_capacity_demand_persistence,
    _run_spillover_graph_recall,
    _run_graph_centrality_dominance,
    _run_var_coverage,
    _run_es_coverage,
]


# ---------------------------------------------------------------------------
# Public entry: run every adapter, surface any per-adapter exception
# ---------------------------------------------------------------------------


def run_all_backtests() -> list[BacktestResult]:
    """Execute every adapter; soft-fail on per-adapter exceptions so a
    single broken module never takes the whole report down."""
    results: list[BacktestResult] = []
    for adapter in ADAPTERS:
        name = adapter.__name__.replace("_run_", "").replace("_", " ").title()
        try:
            results.append(adapter())
        except Exception as exc:  # pragma: no cover - defensive
            results.append(BacktestResult(
                name=name,
                headline_label="status",
                headline_value="error",
                healthy=False,
                summary=f"adapter failed: {exc.__class__.__name__}: {exc}",
            ))
    return results


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def format_text(
    results: list[BacktestResult],
    *,
    verbose: bool = False,
) -> str:
    """Human-readable plain-text report — what the CLI prints by default.

    ``verbose=True`` adds the per-class scorecard rows under each
    validator. Each row is rendered as ``    - <label>: <metric_name> = <value>``.
    Width of the label column is dynamically right-padded so the equals
    signs align.
    """
    width_name = max((len(r.name) for r in results), default=10) + 2
    lines = [
        "=" * 72,
        "Ship Tracker — consolidated backtest report",
        "=" * 72,
        "",
    ]
    for r in results:
        flag = "OK " if r.healthy else "WARN"
        lines.append(f"[{flag}] {r.name.ljust(width_name)} {r.headline_label}: {r.headline_value}")
        lines.append(f"        {r.summary}")
        if verbose and r.scorecard_rows:
            # Right-pad labels so equals signs line up across the validator's rows.
            label_width = max(len(row["label"]) for row in r.scorecard_rows)
            for row in r.scorecard_rows:
                lines.append(
                    f"        - {row['label'].ljust(label_width)}  "
                    f"{row['metric_name']} = {row['value']}"
                )
        lines.append("")
    healthy_count = sum(1 for r in results if r.healthy)
    lines.append("-" * 72)
    lines.append(f"Summary: {healthy_count} of {len(results)} validators healthy.")
    return "\n".join(lines)


def format_json(results: list[BacktestResult]) -> str:
    """One JSON blob — suitable for piping into jq / CI step output."""
    payload = {
        "validators": [
            {
                "name":            r.name,
                "headline_label":  r.headline_label,
                "headline_value":  r.headline_value,
                "healthy":         r.healthy,
                "summary":         r.summary,
                "raw":             r.raw_fields,
            }
            for r in results
        ],
        "healthy_count": sum(1 for r in results if r.healthy),
        "total":         len(results),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def format_markdown(results: list[BacktestResult]) -> str:
    """Markdown table — paste into a docs page."""
    lines = [
        "| Validator | Status | Headline |",
        "| --- | --- | --- |",
    ]
    for r in results:
        flag = "[OK]" if r.healthy else "[WARN]"
        lines.append(
            f"| {r.name} | {flag} | {r.headline_label}: {r.headline_value} |"
        )
    healthy_count = sum(1 for r in results if r.healthy)
    lines.append("")
    lines.append(f"_{healthy_count} of {len(results)} validators healthy._")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Baseline save / compare — drift detection beyond --strict
# ---------------------------------------------------------------------------
#
# `--strict` exits 1 only when a healthy flag is False. That's a one-sided
# gate: a refactor can move chokepoint sign-agreement from 80.5% → 65%
# (still above the 0.55 healthy threshold) and --strict accepts it. The
# baseline workflow catches that drift.
#
# Workflow:
#   1. Operator runs `python -m tools.backtests --save-baseline ref.json`
#      once when the platform is in a known-good state.
#   2. CI / on-demand: `python -m tools.backtests --compare-baseline ref.json`
#      runs the validators again and exits 1 if any healthy flag flipped
#      OR any numeric raw_field drifted beyond its per-metric tolerance.

# Per-metric drift tolerances. Anything not in this map gets the default.
# Keys are raw_field names; values are absolute-drift thresholds.
_DRIFT_TOLERANCE: dict[str, float] = {
    # Rate-like fields (sign-agreement, hit-rate, spreads as fractions):
    # 5 percentage points of drift is "real" — smaller is noise.
    "best_rate":                  0.05,
    "worst_rate":                 0.05,
    "mean_sa_7d":                 0.05,
    "mean_sa_30d":                0.05,
    "delay_sign_agreement":       0.05,
    "spread_strong_vs_weak":      0.05,
    "spread_bullish_vs_bearish":  0.05,
    "spread_critical_vs_low":     0.05,
    # MAE-like fields (delay days): 1.0 day is meaningful.
    "mean_mae_7d":                1.0,
    "mean_mae_30d":               1.0,
    "delay_mae":                  1.0,
    # Spread in days (ETA): 1.0 day.
    "spread_severe_vs_low":       1.0,
}
_DEFAULT_DRIFT_TOLERANCE: float = 0.05


@dataclass
class FieldDrift:
    validator: str
    field_name: str
    baseline: Any
    current:  Any
    delta:    float | None      # None when the comparison is non-numeric
    tolerance: float | None     # None when not a numeric drift check


def save_baseline(results: list[BacktestResult], path: str) -> None:
    """Write the validators' state to a JSON snapshot at ``path``.

    The snapshot captures every result's name, healthy flag, headline
    fields, and raw_fields — the same shape `compare_to_baseline` reads
    back on the other side.
    """
    payload = {
        "validators": [
            {
                "name":            r.name,
                "headline_label":  r.headline_label,
                "headline_value":  r.headline_value,
                "healthy":         r.healthy,
                "summary":         r.summary,
                "raw":             r.raw_fields,
            }
            for r in results
        ],
        "total": len(results),
    }
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2, sort_keys=True)


def compare_to_baseline(
    results: list[BacktestResult],
    baseline_path: str,
) -> list[FieldDrift]:
    """Compare current validator results against a saved baseline.

    Returns a list of ``FieldDrift`` entries — one per detected drift.
    Empty list means no drift. The comparator checks:

      * **healthy flag flips** (any direction)
      * **headline_value** equality (string compare)
      * **raw_fields** numeric drift beyond per-field tolerance
        (``_DRIFT_TOLERANCE``, default ``0.05``)

    Validators present in the baseline but missing from current (or vice
    versa) are reported as drifts too.
    """
    with open(baseline_path, "r", encoding="utf-8") as fp:
        baseline = json.load(fp)
    baseline_by_name = {v["name"]: v for v in baseline.get("validators", [])}
    current_by_name = {r.name: r for r in results}

    drifts: list[FieldDrift] = []

    # 1. Names missing in either direction
    for name in baseline_by_name.keys() - current_by_name.keys():
        drifts.append(FieldDrift(
            validator=name, field_name="<missing>",
            baseline="present", current="absent",
            delta=None, tolerance=None,
        ))
    for name in current_by_name.keys() - baseline_by_name.keys():
        drifts.append(FieldDrift(
            validator=name, field_name="<new>",
            baseline="absent", current="present",
            delta=None, tolerance=None,
        ))

    # 2. Per-validator field drift
    for name in baseline_by_name.keys() & current_by_name.keys():
        baseline_v = baseline_by_name[name]
        current_v = current_by_name[name]

        # healthy flag flip
        if bool(baseline_v.get("healthy")) != bool(current_v.healthy):
            drifts.append(FieldDrift(
                validator=name, field_name="healthy",
                baseline=bool(baseline_v.get("healthy")),
                current=bool(current_v.healthy),
                delta=None, tolerance=None,
            ))

        # headline_value mismatch
        if str(baseline_v.get("headline_value")) != str(current_v.headline_value):
            drifts.append(FieldDrift(
                validator=name, field_name="headline_value",
                baseline=baseline_v.get("headline_value"),
                current=current_v.headline_value,
                delta=None, tolerance=None,
            ))

        # raw_fields drift
        baseline_raw = baseline_v.get("raw") or {}
        current_raw = current_v.raw_fields or {}
        all_keys = set(baseline_raw.keys()) | set(current_raw.keys())
        for key in all_keys:
            b_val = baseline_raw.get(key)
            c_val = current_raw.get(key)
            if isinstance(b_val, (int, float)) and isinstance(c_val, (int, float)):
                tol = _DRIFT_TOLERANCE.get(key, _DEFAULT_DRIFT_TOLERANCE)
                delta = abs(float(c_val) - float(b_val))
                if delta > tol:
                    drifts.append(FieldDrift(
                        validator=name, field_name=key,
                        baseline=b_val, current=c_val,
                        delta=delta, tolerance=tol,
                    ))
            elif b_val != c_val:
                # Non-numeric mismatch (string, bool)
                drifts.append(FieldDrift(
                    validator=name, field_name=key,
                    baseline=b_val, current=c_val,
                    delta=None, tolerance=None,
                ))

    return drifts


def format_drift_report(
    drifts: list[FieldDrift],
    results: list[BacktestResult],
) -> str:
    """Human-readable text summary of a drift comparison."""
    if not drifts:
        return (
            f"Compared {len(results)} validator(s) against baseline — "
            f"no drift detected."
        )
    lines = [
        f"Compared {len(results)} validator(s) against baseline — "
        f"{len(drifts)} drift(s) detected:",
        "",
    ]
    by_validator: dict[str, list[FieldDrift]] = {}
    for d in drifts:
        by_validator.setdefault(d.validator, []).append(d)
    for validator, vd in sorted(by_validator.items()):
        lines.append(f"  {validator}")
        for d in vd:
            if d.delta is not None and d.tolerance is not None:
                lines.append(
                    f"    - {d.field_name}: {d.baseline} → {d.current} "
                    f"(|delta|={d.delta:.4f} > tolerance={d.tolerance:.2f})"
                )
            else:
                lines.append(
                    f"    - {d.field_name}: {d.baseline} → {d.current}"
                )
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# R027 — per-metric baseline DRIFT gate (direction-aware, regression-only)
# ---------------------------------------------------------------------------
#
# `--strict` flips a build red only when a validator's *boolean* healthy flag
# is False. That flag is a hardcoded FLOOR (e.g. best sign-agreement >= 0.55),
# so a real regression that stays above the floor — chokepoint sign-agreement
# sliding 80.5% -> 56% — sails through CI silently. The platform's only
# model-quality guard can't see drift ABOVE the floor.
#
# This gate closes that hole. It pins each validator's HEADLINE metric to a
# committed baseline (`docs/backtest-headline-baseline.json`) and fails only
# when the current value regresses past a per-metric tolerance IN THE WORSENING
# DIRECTION. It is orthogonal to (and stacks on top of) both the floor check
# AND the broader, two-sided `--compare-baseline` snapshot drift above:
#
#   * `--strict`            : floor flags (healthy=False)               [unchanged]
#   * `--compare-baseline`  : two-sided drift on EVERY raw field        [unchanged]
#   * R027 drift gate       : one-sided regression on the HEADLINE metric only
#
# Direction matters: a `higher_better` metric (sign-agreement, recovery rate,
# spread) breaches when it DROPS more than the tolerance; a `lower_better`
# metric (delay MAE) breaches when it RISES more than the tolerance.
# Improvements NEVER breach — tightening the model can never fail the gate.
#
# The baseline is a REAL committed snapshot of the current run's headline
# metrics. After an intentional model change, re-mint it with
# ``--update-headline-baseline`` (the maintainable seam, mirroring the R120
# MODEL_CHANGELOG re-hash step) and commit the new JSON in the same PR.

# Where the committed headline baseline lives. Resolved relative to the repo
# root (this file is ``tools/backtests.py``) so it works regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADLINE_BASELINE_PATH = os.path.join(
    _REPO_ROOT, "docs", "backtest-headline-baseline.json"
)

# The HEADLINE metric per validator: the single number whose regression means
# "the model got materially worse". Maps
#   validator name -> {raw_field: (direction, absolute_tolerance)}
# Direction is "higher_better" or "lower_better". Tolerance is an ABSOLUTE
# drift in the metric's own units (a fraction for rates/spreads, days for MAE).
#
# Tolerances are sized to the metric's natural noise floor:
#   * rate / spread fractions (sign-agreement, recovery, pass-rate, big spreads)
#     -> 0.05 (5 percentage points) — smaller is run-to-run jitter, not drift;
#   * tight calibration spreads (leading-indicator / news bullish-vs-bearish,
#     forecast 30d MAE) -> 0.02, because their healthy band is itself small;
#   * ETA delay MAE (days) -> 0.30 day, the spacing between adjacent quality
#     rungs in that validator's synthetic ladder.
# VaR Coverage is intentionally ABSENT: its basis flips real/synthetic with the
# live price cache, so a committed headline baseline would be non-deterministic.
_HEADLINE_METRICS: dict[str, dict[str, tuple[str, float]]] = {
    "SSI Component Predictiveness":       {"best_rate": ("higher_better", 0.05)},
    "SCHI Dimension Predictiveness":      {"best_rate": ("higher_better", 0.05)},
    "Disruption Forecast Accuracy":       {"mean_sa_30d":  ("higher_better", 0.05),
                                           "mean_mae_30d": ("lower_better",  0.02)},
    "Momentum Ranker Ladder":             {"spread_strong_vs_weak": ("higher_better", 0.05)},
    "Leading Indicators Calibration":     {"spread_bullish_vs_bearish": ("higher_better", 0.02)},
    "News Sentiment Calibration":         {"spread_bullish_vs_bearish": ("higher_better", 0.02)},
    "Vulnerability Scorer Monotonicity":  {"spread_critical_vs_low": ("higher_better", 0.05)},
    "ETA Predictor Accuracy":             {"delay_mae":            ("lower_better",  0.30),
                                           "delay_sign_agreement": ("higher_better", 0.05)},
    "Port Supply Lines Stability":        {"overall_mean_stability": ("higher_better", 0.05)},
    "Company Supply Risk Stability":      {"overall_mean_stability": ("higher_better", 0.05)},
    "SSI Lag-Correlation Recovery":       {"recovery_rate": ("higher_better", 0.05)},
    "Historical Event Replay":            {"pass_rate": ("higher_better", 0.05)},
    "Snapshot Diff Anomaly Recovery":     {"mean_recovery_rate": ("higher_better", 0.05)},
    "Cargo-Flow JSD Stability":           {"pass_rate": ("higher_better", 0.05)},
    "Capacity-Demand Persistence":        {"pass_rate": ("higher_better", 0.05)},
    "Spillover Graph Recall":             {"pass_rate": ("higher_better", 0.05)},
    "Graph Centrality Dominance":         {"pass_rate": ("higher_better", 0.05)},
}


@dataclass
class DriftBreach:
    """One headline metric that regressed past its tolerance, OR a metric with
    no baseline to compare against.

    ``kind`` is ``"regression"`` for a true worsening breach, or
    ``"no baseline"`` when the metric is absent from the committed baseline
    (a maintenance signal — surfaced, but NOT a breach for exit-code purposes).
    """

    validator: str
    metric: str
    direction: str          # "higher_better" | "lower_better"
    baseline: float | None
    current: float | None
    delta: float | None     # signed: current - baseline (None for 'no baseline')
    tolerance: float | None
    kind: str               # "regression" | "no baseline"

    @property
    def is_breach(self) -> bool:
        """Only a true regression counts toward the exit code; 'no baseline'
        is reported for maintainability but never fails the gate on its own."""
        return self.kind == "regression"


def build_headline_baseline(results: list[BacktestResult]) -> dict:
    """Mint the committed headline baseline payload from a current run.

    Walks ``_HEADLINE_METRICS`` and pulls each validator's headline metric
    value out of the live ``results``, recording the value + direction +
    tolerance. A validator/metric not present in this run is simply omitted
    (it will surface as 'no baseline' on the comparison side). Returns the
    JSON-serialisable dict written to :data:`HEADLINE_BASELINE_PATH`.
    """
    by_name = {r.name: r for r in results}
    metrics: dict[str, dict[str, Any]] = {}
    for validator, spec in _HEADLINE_METRICS.items():
        r = by_name.get(validator)
        if r is None:
            continue
        for metric, (direction, tol) in spec.items():
            val = (r.raw_fields or {}).get(metric)
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                continue
            metrics[f"{validator} :: {metric}"] = {
                "validator":  validator,
                "metric":     metric,
                "baseline":   float(val),
                "direction":  direction,
                "tolerance":  float(tol),
            }
    return {
        "schema": "ship.backtest.headline-baseline/1",
        "description": (
            "Per-metric, direction-aware regression baseline (R027). Each entry "
            "pins a validator's HEADLINE metric; --drift-strict fails CI when the "
            "current value regresses past 'tolerance' in the worsening direction. "
            "Improvements never breach. Re-mint with "
            "`python -m tools.backtests --update-headline-baseline` after an "
            "intentional model change."
        ),
        "n_metrics": len(metrics),
        "metrics": metrics,
    }


def save_headline_baseline(results: list[BacktestResult], path: str) -> None:
    """Write the headline baseline JSON for ``results`` to ``path``."""
    payload = build_headline_baseline(results)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2, sort_keys=True)
        fp.write("\n")


def load_headline_baseline(path: str) -> dict:
    """Load the committed headline baseline JSON from ``path``. Raises
    ``OSError`` / ``json.JSONDecodeError`` on a missing/corrupt file."""
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


def check_drift(
    results: list[BacktestResult],
    baseline: dict,
    *,
    tolerance: float | None = None,
) -> list[DriftBreach]:
    """Compare each validator's HEADLINE metric to its committed baseline,
    direction-aware. Returns one :class:`DriftBreach` per regression, plus one
    'no baseline' entry per headline metric the baseline does not cover.

    A breach is recorded when the current value regresses past the tolerance
    IN THE WORSENING DIRECTION:

      * ``higher_better``  breaches when  ``baseline - current  > tol``
        (the metric dropped by more than the tolerance);
      * ``lower_better``   breaches when  ``current  - baseline > tol``
        (the metric rose by more than the tolerance).

    An improvement (or any move within tolerance) NEVER breaches. ``tolerance``
    overrides the per-metric tolerance from the baseline when given (a single
    global override, mainly for tests).

    A headline metric in ``_HEADLINE_METRICS`` that is missing from the
    baseline — or missing from the current run — is recorded as a
    ``kind="no baseline"`` entry (reported, not a breach).
    """
    by_name = {r.name: r for r in results}
    baseline_metrics = (baseline or {}).get("metrics", {}) or {}

    breaches: list[DriftBreach] = []
    for validator, spec in _HEADLINE_METRICS.items():
        r = by_name.get(validator)
        for metric, (direction, default_tol) in spec.items():
            key = f"{validator} :: {metric}"
            entry = baseline_metrics.get(key)
            current = (r.raw_fields or {}).get(metric) if r is not None else None
            current_num = (
                float(current)
                if isinstance(current, (int, float)) and not isinstance(current, bool)
                else None
            )

            # No committed baseline for this metric → maintenance signal.
            if entry is None or not isinstance(entry.get("baseline"), (int, float)):
                breaches.append(DriftBreach(
                    validator=validator, metric=metric, direction=direction,
                    baseline=None, current=current_num, delta=None,
                    tolerance=None, kind="no baseline",
                ))
                continue

            base_val = float(entry["baseline"])
            # Direction from the committed baseline wins (it's what was minted),
            # falling back to the spec; tolerance honours the global override.
            entry_dir = str(entry.get("direction") or direction)
            tol = (
                float(tolerance) if tolerance is not None
                else float(entry.get("tolerance", default_tol))
            )

            # Current run produced no usable value for a metric we DO have a
            # baseline for → can't confirm it held → report as 'no baseline'.
            if current_num is None:
                breaches.append(DriftBreach(
                    validator=validator, metric=metric, direction=entry_dir,
                    baseline=base_val, current=None, delta=None,
                    tolerance=tol, kind="no baseline",
                ))
                continue

            delta = current_num - base_val           # signed
            # A tiny epsilon keeps a drop/rise of EXACTLY the tolerance from
            # flipping to a breach on binary-FP rounding (0.80 - 0.75 lands at
            # 0.05000000000000004 > 0.05). The gate is about real drift, not
            # sub-femto float noise.
            _EPS = 1e-9
            if entry_dir == "lower_better":
                regressed = (current_num - base_val) > tol + _EPS   # rose too much
            else:                                    # higher_better (default)
                regressed = (base_val - current_num) > tol + _EPS   # dropped too much

            if regressed:
                breaches.append(DriftBreach(
                    validator=validator, metric=metric, direction=entry_dir,
                    baseline=base_val, current=current_num, delta=delta,
                    tolerance=tol, kind="regression",
                ))

    return breaches


def format_drift_gate_report(breaches: list[DriftBreach]) -> str:
    """Human-readable summary of the R027 headline-drift gate."""
    regressions = [b for b in breaches if b.is_breach]
    no_baseline = [b for b in breaches if b.kind == "no baseline"]
    lines: list[str] = []
    if regressions:
        lines.append(
            f"Headline-drift gate: {len(regressions)} regression(s) beyond "
            f"tolerance:"
        )
        for b in regressions:
            arrow = "↓" if b.direction == "higher_better" else "↑"
            lines.append(
                f"  [BREACH] {b.validator} :: {b.metric} ({b.direction}) "
                f"{b.baseline} → {b.current} "
                f"(Δ={b.delta:+.4f} {arrow}, tolerance ±{b.tolerance:.4f})"
            )
    else:
        lines.append("Headline-drift gate: no regression beyond tolerance.")
    if no_baseline:
        lines.append("")
        lines.append(
            f"  {len(no_baseline)} headline metric(s) with no committed "
            f"baseline (re-mint with --update-headline-baseline):"
        )
        for b in no_baseline:
            lines.append(f"    - {b.validator} :: {b.metric}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.backtests",
        description=__doc__.splitlines()[0] if __doc__ else "",
    )
    parser.add_argument(
        "--format", "-f",
        choices=("text", "json", "markdown"),
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 if any validator reports healthy=False. "
            "Useful as a CI gate."
        ),
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help=(
            "Include per-class scorecard rows under each validator in "
            "the text output (no effect on json/markdown formats). "
            "Useful for triage when a validator goes red."
        ),
    )
    parser.add_argument(
        "--save-baseline",
        metavar="PATH",
        help=(
            "Run the validators and write a JSON snapshot to PATH. "
            "The snapshot captures every validator's healthy flag + "
            "headline + raw_fields and is the reference for "
            "--compare-baseline. Does not print the normal report."
        ),
    )
    parser.add_argument(
        "--compare-baseline",
        metavar="PATH",
        help=(
            "Run the validators and report drift vs the JSON snapshot "
            "at PATH (previously written by --save-baseline). "
            "Exits 1 on any healthy-flag flip or raw-field drift beyond "
            "the per-metric tolerance."
        ),
    )
    parser.add_argument(
        "--drift-strict",
        action="store_true",
        help=(
            "R027 headline-drift gate. In addition to the --strict floor "
            "checks, exit 1 if any validator's HEADLINE metric has REGRESSED "
            "past its per-metric tolerance vs the committed baseline "
            "(docs/backtest-headline-baseline.json). Direction-aware: "
            "improvements never fail. Implies --strict."
        ),
    )
    parser.add_argument(
        "--headline-baseline",
        metavar="PATH",
        default=HEADLINE_BASELINE_PATH,
        help=(
            "Path to the committed headline baseline JSON used by "
            "--drift-strict / --update-headline-baseline "
            "(default: docs/backtest-headline-baseline.json)."
        ),
    )
    parser.add_argument(
        "--update-headline-baseline",
        action="store_true",
        help=(
            "Re-mint the headline baseline JSON (at --headline-baseline) from "
            "the current run and exit. Run this after an INTENTIONAL model "
            "change, then commit the regenerated file in the same PR — the "
            "maintainable seam for the R027 drift gate."
        ),
    )
    args = parser.parse_args(argv)

    # Mutually exclusive: --save-baseline takes precedence over --compare.
    if args.save_baseline and args.compare_baseline:
        print("error: --save-baseline and --compare-baseline are mutually exclusive",
              file=sys.stderr)
        return 2

    results = run_all_backtests()

    if args.update_headline_baseline:
        try:
            save_headline_baseline(results, args.headline_baseline)
        except OSError as exc:
            print(f"error: cannot write headline baseline "
                  f"{args.headline_baseline!r}: {exc}", file=sys.stderr)
            return 2
        payload = build_headline_baseline(results)
        print(f"Re-minted headline baseline ({payload['n_metrics']} metric(s)) "
              f"to {args.headline_baseline}")
        return 0

    if args.save_baseline:
        try:
            save_baseline(results, args.save_baseline)
        except OSError as exc:
            print(f"error: cannot write baseline {args.save_baseline!r}: {exc}",
                  file=sys.stderr)
            return 2
        print(f"Wrote baseline snapshot for {len(results)} validators to "
              f"{args.save_baseline}")
        return 0

    if args.compare_baseline:
        # Exit 2 (usage/config error, matching the argparse + mutual-exclusion
        # convention) for a missing/corrupt baseline, so CI can distinguish it
        # from the exit-1 "drift detected" contract below.
        try:
            drifts = compare_to_baseline(results, args.compare_baseline)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: cannot read baseline {args.compare_baseline!r}: {exc}",
                  file=sys.stderr)
            return 2
        print(format_drift_report(drifts, results))
        return 1 if drifts else 0

    if args.format == "text":
        print(format_text(results, verbose=args.verbose))
    elif args.format == "json":
        print(format_json(results))
    else:  # markdown
        print(format_markdown(results))

    # --drift-strict implies --strict: the headline-drift gate stacks ON TOP of
    # the floor check, it never replaces it.
    strict = args.strict or args.drift_strict
    floor_failed = strict and any(not r.healthy for r in results)

    drift_failed = False
    if args.drift_strict:
        try:
            baseline = load_headline_baseline(args.headline_baseline)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: cannot read headline baseline "
                  f"{args.headline_baseline!r}: {exc}", file=sys.stderr)
            return 2
        breaches = check_drift(results, baseline)
        # Print the gate report alongside the main report so a red build
        # explains itself.
        print()
        print(format_drift_gate_report(breaches))
        drift_failed = any(b.is_breach for b in breaches)

    if floor_failed or drift_failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
