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

The CLI does NOT take per-module flags — by design, this is the
\"run them all\" path. For tuning a single validator use its Python
entry point directly.
"""
from __future__ import annotations

import argparse
import json
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
    args = parser.parse_args(argv)

    # Mutually exclusive: --save-baseline takes precedence over --compare.
    if args.save_baseline and args.compare_baseline:
        print("error: --save-baseline and --compare-baseline are mutually exclusive",
              file=sys.stderr)
        return 2

    results = run_all_backtests()

    if args.save_baseline:
        save_baseline(results, args.save_baseline)
        print(f"Wrote baseline snapshot for {len(results)} validators to "
              f"{args.save_baseline}")
        return 0

    if args.compare_baseline:
        drifts = compare_to_baseline(results, args.compare_baseline)
        print(format_drift_report(drifts, results))
        return 1 if drifts else 0

    if args.format == "text":
        print(format_text(results, verbose=args.verbose))
    elif args.format == "json":
        print(format_json(results))
    else:  # markdown
        print(format_markdown(results))

    if args.strict and any(not r.healthy for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
