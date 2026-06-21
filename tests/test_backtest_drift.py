"""Defining-property tests for the R027 headline-drift gate (tools/backtests).

The platform's only model-quality CI guard used to be a one-sided FLOOR
(``healthy = metric >= 0.55``): a real regression that stayed above the floor
(80% -> 56% sign-agreement) passed silently. R027 adds a direction-aware,
per-metric baseline drift gate on each validator's HEADLINE metric.

These tests pin the gate's contract:

  * ``check_drift`` flags a ``higher_better`` metric that DROPPED past tol;
  * it does NOT flag one within tol, nor an improvement;
  * it flags a ``lower_better`` metric that ROSE past tol (and not a drop);
  * a metric with no committed baseline → 'no baseline', never a breach;
  * the COMMITTED baseline JSON matches the current run within tolerance
    (so the file is real + CI is green today);
  * ``--drift-strict`` exits non-zero on a synthetic breach and zero today.
"""
from __future__ import annotations

import json

import pytest

from tools.backtests import (
    HEADLINE_BASELINE_PATH,
    BacktestResult,
    DriftBreach,
    build_headline_baseline,
    check_drift,
    load_headline_baseline,
    main,
    run_all_backtests,
)


# ── helpers ───────────────────────────────────────────────────────────────


def _result(name: str, raw: dict) -> BacktestResult:
    """A minimal BacktestResult carrying just a name + raw_fields — all the
    drift gate reads."""
    return BacktestResult(
        name=name,
        headline_label="x",
        headline_value="x",
        healthy=True,
        summary="x",
        raw_fields=raw,
    )


def _baseline(key: str, validator: str, metric: str, value: float,
              direction: str, tol: float) -> dict:
    """A one-entry committed-baseline payload in the on-disk schema shape."""
    return {
        "schema": "ship.backtest.headline-baseline/1",
        "n_metrics": 1,
        "metrics": {
            key: {
                "validator": validator,
                "metric": metric,
                "baseline": value,
                "direction": direction,
                "tolerance": tol,
            }
        },
    }


# A real validator + headline metric that the gate tracks as higher_better.
_HB_VALIDATOR = "SSI Component Predictiveness"
_HB_METRIC = "best_rate"
_HB_KEY = f"{_HB_VALIDATOR} :: {_HB_METRIC}"

# A real validator + headline metric that the gate tracks as lower_better.
_LB_VALIDATOR = "ETA Predictor Accuracy"
_LB_METRIC = "delay_mae"
_LB_KEY = f"{_LB_VALIDATOR} :: {_LB_METRIC}"


# ── 1. higher_better: regression past tol flags; within-tol / improvement not ─


def test_higher_better_regression_beyond_tol_is_breach() -> None:
    """An 80% -> 56% sign-agreement drop (tol 0.05) is a regression breach —
    the exact case the floor gate misses."""
    base = _baseline(_HB_KEY, _HB_VALIDATOR, _HB_METRIC, 0.80, "higher_better", 0.05)
    results = [_result(_HB_VALIDATOR, {_HB_METRIC: 0.56})]
    breaches = check_drift(results, base)
    regs = [b for b in breaches if b.is_breach]
    assert len(regs) == 1
    b = regs[0]
    assert b.kind == "regression"
    assert b.validator == _HB_VALIDATOR and b.metric == _HB_METRIC
    assert b.baseline == 0.80 and b.current == 0.56
    assert b.delta == pytest.approx(-0.24)


def test_higher_better_within_tol_is_not_breach() -> None:
    """A drop SMALLER than the tolerance is jitter, not a regression."""
    base = _baseline(_HB_KEY, _HB_VALIDATOR, _HB_METRIC, 0.80, "higher_better", 0.05)
    results = [_result(_HB_VALIDATOR, {_HB_METRIC: 0.77})]  # -0.03, within 0.05
    assert [b for b in check_drift(results, base) if b.is_breach] == []


def test_higher_better_improvement_never_breaches() -> None:
    """An IMPROVEMENT (metric rose) must never fail the gate."""
    base = _baseline(_HB_KEY, _HB_VALIDATOR, _HB_METRIC, 0.80, "higher_better", 0.05)
    results = [_result(_HB_VALIDATOR, {_HB_METRIC: 0.95})]  # +0.15
    assert [b for b in check_drift(results, base) if b.is_breach] == []


def test_higher_better_exact_tol_edge_is_not_breach() -> None:
    """A drop EXACTLY equal to the tolerance is not a breach (strict >)."""
    base = _baseline(_HB_KEY, _HB_VALIDATOR, _HB_METRIC, 0.80, "higher_better", 0.05)
    results = [_result(_HB_VALIDATOR, {_HB_METRIC: 0.75})]  # -0.05 == tol
    assert [b for b in check_drift(results, base) if b.is_breach] == []


# ── 2. lower_better: rise past tol flags; a drop (improvement) does not ────────


def test_lower_better_rise_beyond_tol_is_breach() -> None:
    """A lower_better metric (delay MAE) RISING past tol is a regression."""
    base = _baseline(_LB_KEY, _LB_VALIDATOR, _LB_METRIC, 0.70, "lower_better", 0.30)
    results = [_result(_LB_VALIDATOR, {_LB_METRIC: 1.20})]  # +0.50 worse
    regs = [b for b in check_drift(results, base) if b.is_breach]
    assert len(regs) == 1
    assert regs[0].direction == "lower_better"
    assert regs[0].delta == pytest.approx(0.50)


def test_lower_better_drop_is_improvement_not_breach() -> None:
    """A lower_better metric DROPPING is an improvement — never a breach,
    however large the move."""
    base = _baseline(_LB_KEY, _LB_VALIDATOR, _LB_METRIC, 0.70, "lower_better", 0.30)
    results = [_result(_LB_VALIDATOR, {_LB_METRIC: 0.10})]  # -0.60, much better
    assert [b for b in check_drift(results, base) if b.is_breach] == []


# ── 3. missing baseline → 'no baseline', not a breach ─────────────────────────


def test_missing_baseline_metric_is_no_baseline_not_breach() -> None:
    """A tracked headline metric absent from the committed baseline is
    reported as 'no baseline' and does NOT fail the gate."""
    empty = {"schema": "ship.backtest.headline-baseline/1",
             "n_metrics": 0, "metrics": {}}
    # A current run that DOES carry the metric, so 'current' is populated.
    results = [_result(_HB_VALIDATOR, {_HB_METRIC: 0.80})]
    breaches = check_drift(results, empty)
    nb = [b for b in breaches if b.validator == _HB_VALIDATOR
          and b.metric == _HB_METRIC]
    assert len(nb) == 1
    assert nb[0].kind == "no baseline"
    assert nb[0].is_breach is False
    assert nb[0].baseline is None and nb[0].current == 0.80


def test_no_baseline_entries_do_not_count_toward_breaches() -> None:
    """An all-empty baseline yields ONLY 'no baseline' entries — zero true
    breaches — so it can never fail the gate by omission."""
    empty = {"metrics": {}}
    results = run_all_backtests()
    breaches = check_drift(results, empty)
    assert breaches, "expected one 'no baseline' entry per tracked headline metric"
    assert all(b.kind == "no baseline" for b in breaches)
    assert all(not b.is_breach for b in breaches)


# ── 4. tolerance override ─────────────────────────────────────────────────────


def test_global_tolerance_override_tightens_the_gate() -> None:
    """A passed-in tolerance overrides the per-metric one: a -0.03 drop is fine
    at tol 0.05 but a breach at tol 0.01."""
    base = _baseline(_HB_KEY, _HB_VALIDATOR, _HB_METRIC, 0.80, "higher_better", 0.05)
    results = [_result(_HB_VALIDATOR, {_HB_METRIC: 0.77})]  # -0.03
    assert [b for b in check_drift(results, base) if b.is_breach] == []
    tight = [b for b in check_drift(results, base, tolerance=0.01) if b.is_breach]
    assert len(tight) == 1


# ── 5. committed baseline matches the live run (file is real + green today) ───


def test_committed_baseline_matches_current_run_within_tolerance() -> None:
    """The checked-in docs/backtest-headline-baseline.json must produce ZERO
    regressions against a fresh run — proving (a) the file is a real snapshot,
    (b) the gate is green today."""
    results = run_all_backtests()
    baseline = load_headline_baseline(HEADLINE_BASELINE_PATH)
    breaches = check_drift(results, baseline)
    regressions = [b for b in breaches if b.is_breach]
    assert regressions == [], (
        "committed headline baseline regressed against the current run: "
        + "; ".join(f"{b.validator}/{b.metric} {b.baseline}->{b.current}"
                    for b in regressions)
    )


def test_committed_baseline_covers_every_tracked_metric() -> None:
    """No tracked headline metric should be 'no baseline' against the committed
    file — every one is pinned, so the file is complete."""
    results = run_all_backtests()
    baseline = load_headline_baseline(HEADLINE_BASELINE_PATH)
    no_baseline = [b for b in check_drift(results, baseline)
                   if b.kind == "no baseline"]
    assert no_baseline == [], (
        "tracked headline metric(s) missing from committed baseline: "
        + ", ".join(f"{b.validator}/{b.metric}" for b in no_baseline)
    )


def test_build_headline_baseline_roundtrips_clean() -> None:
    """Minting a baseline from the current run, then checking that same run
    against it, yields no regressions (the re-mint seam is sound)."""
    results = run_all_backtests()
    minted = build_headline_baseline(results)
    assert minted["n_metrics"] == len(minted["metrics"]) > 0
    regs = [b for b in check_drift(results, minted) if b.is_breach]
    assert regs == []


# ── 6. CLI exit codes ─────────────────────────────────────────────────────────


def test_cli_drift_strict_is_green_today() -> None:
    """--drift-strict against the committed baseline exits 0 on the current
    bundled-synth run."""
    assert main(["--drift-strict"]) == 0


def test_cli_drift_strict_returns_one_on_synthetic_regression(
    monkeypatch, capsys
) -> None:
    """Force a real headline regression (drop best_rate far below baseline) and
    verify --drift-strict exits 1, while plain --strict still exits 0 (the floor
    is unbroken — this is exactly the gap R027 closes)."""
    import tools.backtests as tb

    _original = tb.run_all_backtests

    def _stub() -> list[BacktestResult]:
        results = _original()
        for r in results:
            if r.name == _HB_VALIDATOR:
                # 0.8051 -> 0.40: a 40-pt regression, but still above the
                # 0.55 floor is NOT required — the point is the floor's own
                # healthy flag can be left True while the metric tanks.
                r.raw_fields = dict(r.raw_fields, **{_HB_METRIC: 0.40})
                r.healthy = True  # floor unbroken
        return results

    monkeypatch.setattr(tb, "run_all_backtests", _stub)

    # Plain --strict is blind to it (floor still green).
    assert tb.main(["--strict"]) == 0
    capsys.readouterr()

    # --drift-strict catches it.
    code = tb.main(["--drift-strict"])
    out = capsys.readouterr().out
    assert code == 1
    assert "BREACH" in out
    assert _HB_VALIDATOR in out


def test_cli_drift_strict_missing_baseline_returns_2(tmp_path, capsys) -> None:
    """A missing headline-baseline file is a config error (exit 2), distinct
    from the exit-1 'regression detected' contract."""
    missing = tmp_path / "nope" / "baseline.json"
    code = main(["--drift-strict", "--headline-baseline", str(missing)])
    assert code == 2
    assert "cannot read headline baseline" in capsys.readouterr().err


def test_cli_update_headline_baseline_writes_real_snapshot(tmp_path, capsys) -> None:
    """--update-headline-baseline writes a complete, green snapshot that the
    gate immediately accepts."""
    out = tmp_path / "headline.json"
    code = main(["--update-headline-baseline", "--headline-baseline", str(out)])
    assert code == 0
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["metrics"] and payload["n_metrics"] == len(payload["metrics"])
    # The fresh file must be green against the run it was minted from.
    results = run_all_backtests()
    assert [b for b in check_drift(results, payload) if b.is_breach] == []
