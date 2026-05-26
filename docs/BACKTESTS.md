# Backtest Layer — Validator Catalogue + Health Snapshot

The platform's analytical layer ships **9 deterministic, synth-backfilled
validators**, one per major analytical module. Every validator follows the
same shape: a pure scoring function + a synthetic-history generator + a
defining-property test suite + a UI panel surfacing the result. The
unified `tools.backtests` CLI runs all 9 in one command and CI gates
both unhealthy flips (`--strict`) and numeric drift against this
checked-in baseline (`--compare-baseline docs/backtest-baseline.json`).

This page is the **operator-facing reference** — the table below is
regenerated from the current validator state and shows the headline KPI
for each module on the bundled synthetic-history generators.

To refresh this page after deliberate validator changes:

```bash
# 1. Regenerate the snapshot the CI drift gate compares against:
python -m tools.backtests --save-baseline docs/backtest-baseline.json

# 2. Regenerate this page's status table:
python -m tools.backtests --format markdown > /tmp/table.md

# 3. Paste /tmp/table.md into the "Current health snapshot" section
#    below and commit both files in the same PR.
```

---

## Current health snapshot

| Validator | Status | Headline |
| --- | --- | --- |
| SSI Component Predictiveness | [OK] | Best component: chokepoint (80.5% sign-agreement) |
| SCHI Dimension Predictiveness | [OK] | Best dimension: port_capacity (72.0% sign-agreement) |
| Disruption Forecast Accuracy | [OK] | 30d sign-agreement: 91.7% (MAE 0.039) |
| Momentum Ranker Ladder | [OK] | Monotonic ladder: yes |
| Freight Volatility Classifier | [OK] | Momentum + reversion: both |
| Leading Indicators Calibration | [OK] | Calibrated: yes |
| News Sentiment Calibration | [OK] | Calibrated: yes |
| Vulnerability Scorer Monotonicity | [OK] | Monotonic ladder: yes |
| ETA Predictor Accuracy | [OK] | Monotonic + low MAE: yes (MAE 0.69d, +7.1d spread) |

_9 of 9 validators healthy._

---

## Validator catalogue

### `processing/ssi_component_validation.py` — SSI Components (3-axis)

Validates the Shipping Stress Index's six components (chokepoint,
congestion, weather, rate, anomaly, vulnerability) along three axes:

  * **`validate_ssi_components`** — per-component sign-agreement +
    Pearson r against forward freight-rate moves
  * **`validate_ssi_horizons`** — same scoring sliced across 1/7/14/30/60d
    forecast horizons → (components × horizons) heatmap grid
  * **`compute_component_collinearity`** — pairwise Pearson r across all
    6 components; flags pairs with `|r| ≥ 0.70` as candidates for
    re-weighting (the SSI's static `COMPONENT_WEIGHTS` would be
    double-counting their shared signal)

UI: paired bars + horizon heatmap + collinearity heatmap inside
*Macro Projection → Component Predictiveness* panel.

### `engine/schi_component_validation.py` — SCHI Dimensions (3-axis)

Symmetric companion to the SSI validator, for the Supply Chain Health
Index's six dimensions (port_capacity, freight_cost_pressure,
macro_environment, chokepoint_risk, inventory_cycle, seasonal_factors).
Identical dataclass shapes so consumers template across both.

UI: identical triad directly below the SSI panel in *Macro Projection*.

### `processing/disruption_forecast_backtest.py` — Forecast Accuracy

Scores `processing.disruption_forecast`'s 7d/30d stress projections
against realized stress per route:

  * **`mae_7d` / `mae_30d`** — mean absolute error between forecast and realized
  * **`sign_agreement_7d` / `sign_agreement_30d`** — fraction of windows where
    the forecast direction (forecast > current vs. < current) matched realized

UI: *Disruption Radar → Forecast Accuracy* panel under the 7/30d table.

### `engine/momentum_ranker_backtest.py` — Momentum Ladder

Groups a momentum-signal history by class
(`STRONG_SELL` → `SELL` → `NEUTRAL` → `BUY` → `STRONG_BUY`) and scores:

  * **`mean_forward_return`** per class
  * **`directional_hit_rate`** — in-favour fraction (NEUTRAL pinned to 0.5)
  * **`monotonic_by_signal`** — True when mean return rises STRONG_SELL → STRONG_BUY
  * **`spread_strong_vs_weak`** — STRONG_BUY mean − STRONG_SELL mean

UI: *Data Health → Signal Validation* section + per-class scorecard table.

### `processing/freight_volatility_backtest.py` — Regime + Reversion

Validates `processing.freight_volatility`'s regime classifier
(TRENDING_UP / TRENDING_DOWN / BREAKOUT / RANGING) and mean-reversion
signal (OVERSOLD / NEUTRAL / OVERBOUGHT):

  * **`momentum_works`** — TRENDING_UP mean > 0 AND TRENDING_DOWN mean < 0
  * **`mean_reversion_works`** — OVERSOLD mean > 0 AND OVERBOUGHT mean < 0

### `processing/leading_indicators_backtest.py` — Leading-Indicator Calibration

Per-signal-class lead-time accuracy. For each
BULLISH / BEARISH / NEUTRAL classification:

  * **`mean_forward_demand_pct`** — realized demand move at the indicator's stated lead time
  * **`signals_calibrated`** — BULLISH mean > 0 AND BEARISH mean < 0

### `processing/news_sentiment_backtest.py` — News Sentiment Calibration

Per-sentiment-label realized forward freight-rate move. Same pattern as
leading indicators but the realized signal is freight-rate movement
rather than demand.

### `processing/vulnerability_scorer_backtest.py` — Vulnerability Ladder

Per-vulnerability-label realized disruption rate. Validates that the
LOW → MODERATE → HIGH → CRITICAL ladder is monotonically rising in
realized disruption frequency.

### `processing/eta_predictor_backtest.py` — ETA Prediction Accuracy

Two scoring axes for `processing.eta_predictor`:

  * Scalar: **`delay_mae`** + **`delay_sign_agreement`** on the predicted-vs-realized delay days
  * Categorical: per-congestion-risk-label realized delay; **`monotonic_by_label`**
    flag verifies the LOW → SEVERE ladder rises

---

## Common pattern

Every validator ships with a `synthesize_*_history(seed, quality, ...)`
deterministic generator. The quality knob is load-bearing for the
property test suite:

  * At `quality=1.0` the validator's primary calibration / monotonicity
    flag must flip **True** and the spread/MAE hits the seeded ladder
  * At `quality=0.0` the per-class means must collapse into the
    noise band (no spurious calibration on random data)

This is the load-bearing property test: it proves the validator isn't
trivially passing.

## CI integration

`.github/workflows/ci.yml` runs two gates after `pytest`:

```yaml
- name: Run consolidated backtest health gate (strict)
  run: python -m tools.backtests --format text --strict

- name: Run baseline drift gate
  run: python -m tools.backtests --compare-baseline docs/backtest-baseline.json
```

`--strict` is one-sided (catches healthy=False flips). The
`--compare-baseline` gate is sharper — catches numeric drift in either
direction even when the validator stays above the healthy threshold.
Per-metric tolerances live in `tools/backtests.py::_DRIFT_TOLERANCE`
(±5pp on rates, ±1.0d on MAEs).

## Operator triage

When a validator goes red in CI, drop the `--verbose` flag for the
per-class scorecard inline:

```bash
python -m tools.backtests --verbose
```

Each validator block then prints its full per-class breakdown
underneath — chokepoint at 80.5%, rate at 70.9%, congestion at 66.1%,
etc. — so the offending class is immediately visible without dropping
into a Python REPL.
