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
    )


def _run_schi_components() -> BacktestResult:
    from engine.schi_component_validation import validate_schi_components
    r = validate_schi_components()
    best = next(sc for sc in r.scorecards if sc.component == r.best_component)
    worst = next(sc for sc in r.scorecards if sc.component == r.worst_component)
    healthy = best.sign_agreement_rate >= 0.55
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
    )


def _run_disruption_forecast() -> BacktestResult:
    from processing.disruption_forecast_backtest import backtest_disruption_forecast
    r = backtest_disruption_forecast()
    # Healthy when 30d sign-agreement is materially above random.
    healthy = r.mean_sign_agreement_30d >= 0.55
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
    )


def _run_momentum_ranker() -> BacktestResult:
    from engine.momentum_ranker_backtest import backtest_momentum_signals
    r = backtest_momentum_signals()
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
    )


def _run_leading_indicators() -> BacktestResult:
    from processing.leading_indicators_backtest import backtest_leading_indicators
    r = backtest_leading_indicators()
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
    )


def _run_news_sentiment() -> BacktestResult:
    from processing.news_sentiment_backtest import backtest_news_sentiment
    r = backtest_news_sentiment()
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
    )


def _run_vulnerability_scorer() -> BacktestResult:
    from processing.vulnerability_scorer_backtest import backtest_vulnerability_scorer
    r = backtest_vulnerability_scorer()
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
    )


def _run_eta_predictor() -> BacktestResult:
    from processing.eta_predictor_backtest import backtest_eta_predictor
    r = backtest_eta_predictor()
    # Healthy when the monotonicity ladder holds + the MAE is plausible
    # (under 3 days — well above quality=1.0's collapse, well under
    # quality=0.0's noise floor)
    healthy = bool(r.monotonic_by_label) and r.delay_mae < 3.0
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


def format_text(results: list[BacktestResult]) -> str:
    """Human-readable plain-text report — what the CLI prints by default."""
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
    args = parser.parse_args(argv)

    results = run_all_backtests()
    formatter = {
        "text":     format_text,
        "json":     format_json,
        "markdown": format_markdown,
    }[args.format]
    print(formatter(results))

    if args.strict and any(not r.healthy for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
