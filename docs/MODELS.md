# Models

A methodology reference for every transparent model, forecaster and scorer
that ships with the platform. The code is the source of truth and every claim
below is grounded in the modules / functions / tables it names.

The platform is a **modeled / demo** environment: every series the models
consume is either synthetic or modeled, and every result is stamped accordingly
through `data/quality.py`. The equity layer is **rule-based idea generation**
labelled "modeled — not investment advice". No fitted ML touches the cascade,
the direction lookup, or the conviction sum; the only ML in the whole stack is
the rate forecaster's GBR / Ridge pipeline (see below), and even there every
feature is named and every importance is mapped to a human label.

The unifying design principle: **every weight is published, every term is
named, every threshold is documented as an explicit constant.** A reader can
audit any number end-to-end by following the code references in each section.

## Contents

1. [Rate forecasting](#rate-forecasting)
   * [`processing/monte_carlo.py`](#monte_carlo--ornstein-uhlenbeck--jumps)
   * [`processing/forecaster.py`](#forecaster--deseasonalize--linear-trend--horizon-widening-ci)
   * [`processing/rate_forecaster.py`](#rate_forecaster--gbr--ridge-ml-pipeline)
2. [Disruption & congestion forecasting](#disruption--congestion-forecasting)
   * [`processing/congestion_predictor.py`](#congestion_predictor--per-port-mean-reversion--macro-pressure--confidence-bands)
   * [`processing/disruption_forecast.py`](#disruption_forecast--volatility-scaled-rate--symmetric-mc-tails--oversupply-cross-signal)
3. [The Shipping Stress Index](#the-shipping-stress-index)
   * [`processing/shipping_stress_index.py`](#shipping_stress_index--5-component-blend-with-prominence-weighting)
4. [The Disruption Alpha cascade](#the-disruption-alpha-cascade)
   * [`processing/disruption_cascade.py`](#disruption_cascade--equity-idea-scorer)
5. [Signal validation](#signal-validation)
   * [`processing/signal_validation.py`](#signal_validation--hit-rate-scorecard-vs-equal-weight-baseline)

---

## Rate forecasting

Three forecasters, each with a different temperament. They are deliberately
overlapping rather than redundant: each is honest about a different kind of
uncertainty.

| Module                          | What it answers                                          | Process                                            |
|---------------------------------|----------------------------------------------------------|----------------------------------------------------|
| `processing/monte_carlo.py`     | What is the **distribution** of plausible 30 / 90d rates? | OU mean-reversion in log-space + Poisson jumps     |
| `processing/forecaster.py`      | What is the **point** 30 / 60 / 90d rate, with bands?    | Deseasonalize → linear trend → re-apply seasonality |
| `processing/rate_forecaster.py` | What is the **direction** and which features drive it?   | GBR (30d) + Ridge (7d) on a documented feature set |

### `monte_carlo` — Ornstein-Uhlenbeck + jumps

**Function.** `simulate_freight_rates(freight_data, route_id, n_simulations=500,
forecast_days=90, volatility_override=None) -> MonteCarloResult | None`.
Multi-route helpers: `simulate_all_routes(...)` and
`get_highest_upside_routes(...)`; risk wrapper:
`get_risk_adjusted_opportunity(result, risk_free_rate=0.04)`.

**Why not GBM.** Container freight rates are cyclical and mean-reverting:
oversupply pulls rates down → carriers idle capacity → the market tightens →
rates recover. A plain Geometric Brownian Motion has *no reversion force*, so
long-horizon paths fan out and drift away from any economically plausible
level.

**Process.** The discrete-time recursion of an OU process on the **log-rate**:

```
d(ln S) = θ · (μ_long − ln S) · dt  +  σ · dW   (+ Poisson jump)
```

Run in log-space so the simulated rate can never go negative.

| Parameter     | What it is                              | How it is set                                                                                       |
|---------------|-----------------------------------------|-----------------------------------------------------------------------------------------------------|
| `θ` (theta)   | Reversion speed per day                 | `_DEFAULT_REVERSION_SPEED = 0.025` ⇒ half-life ≈ 28 days — gentle, not snap-back                    |
| `μ_long`      | Long-run equilibrium log-rate           | Trailing-180-day blend: `0.5 × mean + 0.5 × median` (`_MU_MEAN_WEIGHT = 0.5`) — responsive but robust to spikes |
| `σ`           | Per-day log-rate volatility             | Historical daily log-return std, or `volatility_override / √252` when supplied                      |
| Jump term     | Discrete disruption shocks              | Poisson intensity `_JUMP_INTENSITY_PER_DAY = 0.012` (≈ 1 jump per 83 days), symmetric, `_JUMP_STD_LOG = 0.045` |

The jump term adds a realistic fat tail without dominating the diffusion. The
random seed is derived deterministically from `current_rate`, so the Monte
Carlo tab is reproducible across reruns for the same input — matching the
platform's wider deterministic-seed convention.

**Output.** `MonteCarloResult` carries the simulated path matrix, percentile
bands (`p5 / p25 / p50 / p75 / p95`), end-of-horizon metrics
(`expected_rate_90d`, `bull_case_90d` = P90, `bear_case_90d` = P10,
`confidence_interval_90d`, `var_95`), the up/down probability, and four
**process-transparency** fields (`process`, `reversion_speed`, `long_run_rate`,
`daily_volatility`) so callers can inspect the dynamics.

### `forecaster` — deseasonalize → linear trend → horizon-widening CI

**Function.** `forecast_all_routes(freight_data, seasonal_adjustments=None)`
returns a list of `RateForecast` per route. Per-route work happens in
`_forecast_route(route_id, route_name, df, seasonal_override)`; seasonal
factors are looked up by date via
`processing.seasonal.get_seasonal_adjustment(...)`.

**Why not just fit a line through the raw history.** Container freight rates
carry strong calendar seasonality (the pre-CNY export surge, the CNY slowdown,
the Aug–Oct peak season, the post-holiday lull). Fitting a trend straight
through that seasonality lets the season-of-the-moment tilt the slope.

**Process — three steps.**

1. **Deseasonalize.** Each historical observation is divided by its date's
   seasonal factor (the adjustment in `[-0.15, +0.15]` converted to a
   multiplicative `1 + adj` factor). The OLS fit runs on the deseasonalized
   series, so the slope reflects underlying trend, not where in the calendar
   the history happens to sit.
2. **Re-apply seasonality at the forecast horizon.** The deseasonalized trend
   is extrapolated to the 30 / 60 / 90-day horizon and then **multiplied back**
   by the seasonal factor of *the forecast date*. A forecast that lands in
   peak season reflects peak season; one landing in the CNY slowdown reflects
   that softness — regardless of where "today" sits.
3. **Horizon-widening CI.** Forecast error grows with horizon, so the
   confidence band is the residual std scaled by
   `sqrt(horizon / _BAND_REFERENCE_HORIZON)` (`_BAND_REFERENCE_HORIZON = 30.0`).
   The 90d band is ~√3× the 30d band — the standard random-walk fan-out. Both
   `upper_30d` / `lower_30d` *and* `upper_90d` / `lower_90d` are returned, so
   the UI can show the widening explicitly.

Hard sanity caps prevent a runaway linear trend from going to zero or
infinity: every forecast is clamped to
`[current_rate × _FORECAST_FLOOR_MULT, current_rate × _FORECAST_CEIL_MULT]`
(`0.30 × … 3.00 ×`). Confidence is graded `"High" / "Medium" / "Low"` from
R² and sample count; the plain-English `methodology` field on `RateForecast`
summarises the trend, seasonal tilt and band-widening in one paragraph that
the UI surfaces verbatim.

### `rate_forecaster` — GBR + Ridge ML pipeline

**Function.** `forecast_route(route_id, route_name, rate_df, macro_data,
cache_ttl_hours=6.0) -> RateForecast | None`, called in batch by
`forecast_all_routes(freight_data, macro_data) -> dict[str, RateForecast]`.

**The only ML in the stack** — and even here every feature is named and every
importance is mapped to a human label (`_FEATURE_LABELS`).

**Feature set** (one row per "current observation" — `_build_features(...)`):

| Family       | Features                                                                                  |
|--------------|-------------------------------------------------------------------------------------------|
| Momentum     | `mom_7d`, `mom_14d`, `mom_30d`, `mom_60d` — rolling fractional changes                    |
| Calendar     | `month`, `week_of_year`, `is_peak_season`, `is_cny` — seasonality dummies                 |
| Baltic Dry   | `bdi_level`, `bdi_chg_30d`, `bdi_chg_90d` — from FRED `BDIY`                              |
| Energy       | `wti_price` — from FRED `DCOILWTICO` (bunker-fuel proxy; default 75 when absent)          |
| Demand       | `pmi_level` — from `IPMAN` / `NAPMPI` (default 100 when absent)                           |
| Mean rev.    | `mean_rev_z` — current rate's z-score vs trailing 90-day window                            |
| Capacity     | `capacity_proxy = 1 + mom_30d` — simple bounded interpretation                            |

**Training set.** `_build_training_dataset(rate_series, macro_data, horizon)`
builds a rolling-window supervised set: at each in-sample index `i`, features
computed from `rates[:i]` predict `rates[i + horizon]`. Stride is sized so
each route trains on at most ~40 windows.

**Horizon-specific model choice.**

| Horizon | Model                                                                                  | Why                                                  |
|---------|----------------------------------------------------------------------------------------|------------------------------------------------------|
| 7-day   | `Ridge(alpha=10.0)`                                                                    | Stable, avoids overfit on short horizons             |
| 30-day  | `GradientBoostingRegressor(n_estimators=80, max_depth=3, learning_rate=0.08, …)`        | Captures non-linear macro / momentum interactions    |
| 90-day  | Same GBR (trained against the 90-day target, but predicted with 30-day model defaults)  | Continuity with the 30d model — caps any drift       |

Out-of-sample R² is estimated by cross-validation
(`cross_val_score(..., cv=min(5, n // 4))`) and reported on `RateForecast`.
The 30-day GBR's confidence interval comes from `_gbr_confidence_interval(...)`,
which reads the *spread of late-stage staged predictions* as an uncertainty
proxy (with a ±15% fallback when staged predictions are unavailable).

`_extract_key_drivers(...)` reads `feature_importances_` (or `|coef_|` for
Ridge), sorts descending, and returns the top three feature labels via
`_FEATURE_LABELS` — so the UI can name *why* the forecast leans the way it
does. The result is cached per route for `cache_ttl_hours = 6.0` to avoid
retraining within a session. `direction` is graded `"Rising" | "Falling" |
"Stable"` from the 30d % move with a 3% dead-band; `direction_confidence`
blends R² with signal strength (each saturating at 1.0).

---

## Disruption & congestion forecasting

### `congestion_predictor` — per-port mean-reversion + macro pressure + confidence bands

**Function.** `predict_congestion(port_locode, current_congestion,
macro_data=None, congestion_history=None) -> CongestionForecast`. Multi-port
helper: `predict_all_ports(port_results, macro_data) -> dict[str,
CongestionForecast]`.

**Three documented refinements over a stateless smoother.**

**1. Per-port mean-reversion baseline (`_PORT_BASELINE`).** The 30-day forecast
no longer reverts every port toward a flat 0.5. Busy hubs structurally clear
slower than quiet feeders, so each tracked port has its own equilibrium
congestion target — surfaced on the result as `reversion_baseline`.

| Tier                 | Baseline   | Members                                                                 |
|----------------------|------------|-------------------------------------------------------------------------|
| Mega-hubs            | 0.58–0.62  | CNSHA (0.62), SGSIN (0.60), NLRTM (0.58), USLAX (0.60), USLGB (0.58), CNNBO (0.59) |
| Large gateways       | 0.52–0.55  | BEANR, AEJEA, USNYC, USSAV, GBFXT                                       |
| Mid-size / regional  | 0.48–0.50  | JPYOK, GRPIR, BRSAO, LKCMB                                              |
| Feeder / short-sea   | 0.42       | MATNM                                                                   |
| Unknown / fallback   | `_DEFAULT_BASELINE = 0.55` | — slightly above the 0.5 midpoint because a *tracked* container port is non-trivial by selection |

The 30-day step blends the 14d prediction with the per-port baseline at a
modest weight — fast enough to anchor the long horizon, slow enough not to
snap.

**2. Magnitude-aware macro pressure (`_macro_pressure`).** Pressure is
continuous, not a yes / no threshold:

* **BDI channel.** `BDI_change_pct` (preferred) scales linearly up to
  `_BDI_SATURATION_PCT = 0.20`; only a *rising* BDI adds pressure (a falling
  dry-bulk market does not relieve container-port congestion). Maxes out at
  `_BDI_MAX_PRESSURE = 0.04` congestion points. The coarse legacy `BDI_rising`
  bool degrades to a half-strength move.
* **PMI channel.** Pressure scales with distance *above* the neutral 50 line,
  saturating at `_PMI_SATURATION_DISTANCE = 8.0`; capped at
  `_PMI_MAX_PRESSURE = 0.03`. A PMI at or below neutral contributes zero.

Both caps are individual, so even an extreme print can only nudge — never
dominate — the forecast.

**3. Confidence bands (`_confidence_band`).** Each horizon now carries a
`(low, high)` band derived from realised volatility:

* `_congestion_volatility(history)` reads the std of successive-observation
  changes when ≥ 3 finite, in-range points are supplied; otherwise it falls
  back to `_DEFAULT_VOLATILITY = 0.06`.
* Band half-width = `_BAND_Z × volatility × sqrt(horizon / 7)` with
  `_BAND_Z = 1.28` (an ~80% interval — moderate, not 95%) and
  `_BAND_REFERENCE_HORIZON = 7.0`. The 30d band is ~2× wider than the 7d
  band, mirroring the rate forecaster's `√(horizon)` widening.

`CongestionForecast` surfaces `congestion_volatility` and `reversion_baseline`
explicitly so the UI can show the band's derivation rather than asking the
user to trust it. `driving_factors` describe pressure by tier (`Strong /
Moderate / Mild`) rather than by which boolean fired.

### `disruption_forecast` — volatility-scaled rate + symmetric MC tails + oversupply cross-signal

**Function.** `forecast_route_stress(route_id, freight_data, macro_data,
route_results, current_stress=None) -> StressForecast`. Batch:
`forecast_all_stress(freight_data, macro_data, route_results,
stress_report=None) -> list[StressForecast]`. Reads `stress_report`
duck-typed (`.route_stress` items expose `.route_id` / `.stress_score`) to
seed each route's current stress, so the import graph stays acyclic.

**Thin orchestration**, not a new forecaster. It blends three already-existing
forward signals into 7- / 30-day stress numbers on a 0–1 scale, with documented
weights asserted to sum to 1.0:

```
_W_CURRENT    = 0.55   # persistence — stress is sticky
_W_CONGESTION = 0.28   # how the physical bottleneck is trending
_W_RATE       = 0.17   # rate direction as a capacity-tightness proxy
```

**Three refinements** over the original fixed-weight blend.

**1. Volatility-scaled rate signal (`_route_rate_volatility`).** A raw rate
move is first normalised by the route's *own* historical monthly log-return
volatility, then by `_RATE_VOL_SATURATION_SIGMAS = 2.5` to reach a
full-strength (±1) signal. So a 5% move on a calm lane is a big z-score; the
same 5% on a swingy lane is barely a blip. The per-route vol is clamped to
`[_RATE_VOL_FLOOR = 0.03, _RATE_VOL_CEIL = 0.40]` so a near-flat synthetic
series cannot make every move look enormous, and one wild route cannot make
real moves invisible. The z-score is surfaced as `rate_signal_z`.

**2. Symmetric Monte Carlo tails (`_mc_tails`).** Both tails of the 30-day MC
distribution feed the blend:

* `p90_upside` — `(P90 − current) / current`, floored at 0. Capacity tightening
  → upward stress push.
* `p10_downside` — `(P10 − current) / current`, capped at 0. Capacity glut /
  blank-sailing risk → downward stress push.

Each tail is one-signed and each saturates at `_MC_TAIL_SATURATION = 0.30`
(a 30% tail move is full-strength). Their net `tail_push` can revise stress
either way — not just upward like the original.

**3. Structural-oversupply cross-signal.** When destination-port congestion
sits at or above `_OVERSUPPLY_CONGESTION_MIN = 0.60` *while* there is genuine
downward rate evidence — either the central rate forecast at or below
`_OVERSUPPLY_RATE_MAX = -0.03`, *or* the MC P10 tail at or below
`_OVERSUPPLY_P10_MAX = -0.08` — that combination reads as idle capacity stuck
behind a bottleneck. The flag (`structural_oversupply = True`) applies a small
explicit `_OVERSUPPLY_STRESS_RELIEF = 0.05` downward adjustment and is called
out verbatim in `narrative` and `drivers`. The rate-OR-tail logic makes the
rule robust to a near-flat central forecast that nonetheless carries a fat
left tail.

**Output.** `StressForecast` carries the three core fields
(`current_stress`, `stress_7d`, `stress_30d`, all in `[0, 1]`), the
trend label (`"Improving" / "Stable" / "Worsening"` against
`_TREND_BAND = 0.04`), the rate fraction (`rate_forecast_pct`) and the
MC tails (`mc_p90_upside`, `mc_p10_downside`), plus four refinement-
transparency fields (`rate_volatility`, `rate_signal_z`,
`structural_oversupply`) and a plain-English `narrative` summarising the
projection.

Every public function tolerates empty `freight_data` and returns neutral
defaults rather than crashing. A failure in one route never aborts the
batch — `forecast_all_stress` emits a safe neutral entry for it instead.

---

## The Shipping Stress Index

### `shipping_stress_index` — 5-component blend, with prominence weighting

**Function.** `compute_shipping_stress(freight_data, macro_data, port_results,
route_results, voyage_fleet=None) -> ShippingStressReport`. The fleet-wide
composite read on what is breaking. Every public function tolerates empty
inputs and returns neutral defaults.

**The blend (`COMPONENT_WEIGHTS`, asserted ∑ = 1.0).**

| Component       | Weight | Source                                                                  | Notes                                                |
|-----------------|--------|-------------------------------------------------------------------------|------------------------------------------------------|
| `chokepoint`    | 0.32   | `chokepoint_analyzer.compute_chokepoint_risk_score` + `get_current_active_disruptions` | Max chokepoint risk touching the lane + a modest compounding bump for each extra disrupted chokepoint |
| `congestion`    | 0.22   | `congestion_predictor.predict_congestion`                                | Destination-port 7-day prediction; unknown ⇒ 0.5 (genuinely unknown, not absent) |
| `weather`       | 0.18   | `weather_risk.compute_route_weather_risk`                                | Lane's `current_risk_score` straight onto SSI scale  |
| `rate`          | 0.18   | `freight_data` 30-day abs % move                                         | A spike *or* crash both register as stress; a 40% move maps to full stress |
| `vulnerability` | 0.10   | `vulnerability_scorer.score_vulnerability`                               | Structural lane fragility — a slow-moving baseline   |

The per-route composite `stress_score` is the weighted sum, clamped to
`[0, 1]`. `dominant_driver` is the component contributing the most *weighted*
stress (not the largest raw component) — mapped to a human label via
`_DRIVER_LABELS` and used downstream by the cascade to select a rule row and
a conviction weight set.

**Prominence weighting (`_PROMINENT_ROUTES`).** The fleet-wide `overall_ssi`
is a weighted average of per-route stress with the two highest-volume global
lanes — `transpacific_eb` and `asia_europe` — given a route weight of 2.0
versus a default of 1.0. Their stress matters disproportionately, so the
SSI moves more on them.

**Banding (`_SSI_BANDS`).** The overall SSI is binned into one of four bands,
each carrying a label and hex colour the UI surfaces in the gauge:

| Band       | Score range   | Colour    |
|------------|---------------|-----------|
| Calm       | `[0.00, 0.25)` | `#2e9e6e` |
| Elevated   | `[0.25, 0.45)` | `#c9962b` |
| Stressed   | `[0.45, 0.65)` | `#f97316` |
| Severe     | `[0.65, 1.00]` | `#c0392b` |

**Output.** `ShippingStressReport` carries `overall_ssi`, the banded
`ssi_label` / `ssi_color`, the per-route `route_stress` list (sorted
worst-first), per-component fleet averages (`component_scores`), a
`top_disruptions` list drawn from `engine.alert_engine.generate_alerts`
(CRITICAL + WARNING first, supplemented by the worst-stressed lanes), and an
ISO `data_timestamp`. When `voyage_fleet` is supplied each `RouteStress` also
reports its `delayed_voyage_count` (status in `_DELAYED_STATUSES = {"Minor
Delay", "Major Delay"}`).

---

## The Disruption Alpha cascade

### `disruption_cascade` — equity-idea scorer

**Function.** `score_equity_ideas(stress_report, exposure_matrix, stock_data,
insights=None) -> list[EquityIdea]`. Per-ticker work happens in
`_score_one_idea(...)`, orchestrating the cascade walk, direction lookup and
conviction sum. Summary roll-up: `cascade_summary(ideas) -> dict`.

This is the **"Conclude"** stage. The full chain (voyage → SSI → exposure →
cascade) is documented in [`DISRUPTION_ALPHA.md`](DISRUPTION_ALPHA.md); this
section is the per-engine methodology.

### Walking the chain

For every company in `exposure_matrix.COMPANY_COMMODITY_EXPOSURE`,
`_company_cascade(...)` walks one hop per `(commodity, route)` pair. Each hop
is a `CascadeLink` with:

```
contribution = route_stress × cargo_share × exposure_weight
```

* `exposure_weight` — the company's weight on the HS category, from
  `company_commodity_weights(ticker)`.
* `route_stress` — the lane's `RouteStress.stress_score` (from the SSI).
* `cargo_share` — the *actual* share of that commodity in the route's cargo
  mix, from `cargo_analyzer.get_route_cargo_mix(route_id, {})`. Routes that
  carry more of the commodity therefore contribute proportionally more.

**Real cargo shares, not uniform.** `_route_cargo_shares(...)` reads the real
mix per route; the uniform `exposure_weight / N` split is taken **only** as a
fallback when *no* route yields a usable mix for a commodity. A route whose
mix simply lists the commodity at 0% keeps a genuine 0.0 share — that's not a
reason to discard the real-mix path for the other routes. When the fallback
fires the idea raises a `risk_flags` entry stating per-lane contributions are
approximate, and `supporting_signals` names the source explicitly
(`actual per-route cargo mix` vs `uniform 1/N cargo-share fallback`).

Hops are sorted worst-first; the `cascade_magnitude` is their summed
`contribution`; the `dominant_driver_key` is taken from the single
highest-contribution link's route (its `RouteStress.dominant_driver`,
normalised through `_driver_key(...)` against `_DRIVER_KEYS`).

### Direction — `_DIRECTION_RULES`

The crux of the "transparent, not a black box" requirement. An explicit table
keyed by `(company sector, dominant stress driver)`, yielding
`(sign, rationale)`:

* `sign` — `+1` bullish, `-1` bearish, `0` neutral. Multiplied onto the
  cascade magnitude to give a signed cascade score.
* `rationale` — a one-line plain-language justification, surfaced verbatim in
  the `EquityIdea.thesis` so every sign is self-documenting.

Container liners / lessors: chokepoint, congestion, rate and weather are all
`+1` (each one tightens effective capacity); vulnerability alone is `0` (a
latent risk, not a catalyst). Dry-bulk carriers: same `+1` for chokepoint,
congestion, rate, weather (they lengthen bulker voyages and tighten the bulk
market) and `0` for vulnerability. Dry-bulk *magnitude* on physical drivers is
additionally dampened by `_DRY_BULK_DRIVER_DAMPEN = 0.65` because their primary
channel is the commodity-demand side handled by the exposure weighting.

Unknown `(sector, driver)` pairs degrade to Neutral — never guess.

A separate **fuel-cost overlay** (`_FUEL_COST_RULE`, sign `-1`) handles the
USO channel: when USO has risen more than `_FUEL_OVERLAY_THRESHOLD = 0.03`
over 30 days, the overlay can drag a bullish cascade to Neutral or push a
neutral cascade Bearish — but never strengthens a bullish call. The overlay
adjusts only the *published* direction; `cascade_direction` (the pre-overlay
verdict) is preserved so corroboration (commodity ETF, decision-engine
insight) is still measured against the physical cascade.

### Conviction — `_CONVICTION_WEIGHT_SETS`

A transparent weighted sum of four named, normalised-to-`[0, 1]` terms, each
scaled by a published weight:

| Term           | Definition                                                                                              |
|----------------|---------------------------------------------------------------------------------------------------------|
| `cascade`      | `cascade_magnitude / _CASCADE_FULL_SCALE`, where `_CASCADE_FULL_SCALE = 1.2` (calibrated to the real-cargo-mix magnitude scale so a genuinely severe cascade reaches a near-full term) |
| `agreement`    | `agreement_count / _AGREEMENT_FULL_COUNT` (= 4). Signals counted: the cascade itself (1, when non-empty and non-Neutral), a commodity-ETF read in the same direction, an `engine.scorer` insight naming the ticker |
| `etf_confirm`  | 1.0 if a driving commodity's ETF move agrees with the *cascade* direction (not the fuel-adjusted published one) else 0.0 — so a cost overlay cannot zero out genuine demand-side confirmation |
| `vulnerability`| mean structural fragility of the driving routes × per-driver persistence (see below)                   |

**The weights are not one fixed set.** `_CONVICTION_WEIGHT_SETS` holds five
hand-authored, per-driver weight sets, each asserted at import to sum to 1.0:

| Set            | `cascade` | `agreement` | `etf_confirm` | `vulnerability` | Selected for                                          |
|----------------|----------:|------------:|--------------:|----------------:|-------------------------------------------------------|
| `default`      | 0.42      | 0.22        | 0.20          | 0.16            | congestion (the balanced operational story) and unknown drivers |
| `chokepoint`   | 0.52      | 0.18        | 0.16          | 0.14            | chokepoint reroutes — the physical cascade IS the signal |
| `rate`         | 0.30      | 0.30        | 0.28          | 0.12            | freight-rate dislocations — a price signal that wants independent confirmation |
| `weather`      | 0.34      | 0.26        | 0.24          | 0.16            | weather events — short-lived, cascade magnitude overstates a transient hit |
| `vulnerability`| 0.34      | 0.20        | 0.16          | 0.30            | structural-vulnerability-driven stress with no acute trigger |

Selection is a plain dictionary lookup on the dominant driver key
(`_DRIVER_WEIGHT_SET`). The **name** of the set actually used is emitted
verbatim in `EquityIdea.supporting_signals[0]` and persisted on the dataclass
(`conviction_weight_set`) so the score stays reproducible by reading the
output alone.

**Persistence-weighted vulnerability (`_DRIVER_PERSISTENCE`).** Fragility only
earns conviction in proportion to how long the stress is likely to last. The
vulnerability term is multiplied by a per-driver factor in `(0, 1]`:

| Driver         | Factor | Why                                                                    |
|----------------|--------|------------------------------------------------------------------------|
| `chokepoint`   | 0.95   | Closures / reroutes resolve slowly — near-full credit                  |
| `vulnerability`| 1.00   | Structural fragility is by definition persistent                       |
| `congestion`   | 0.70   | Port backlogs clear over weeks                                         |
| `rate`         | 0.55   | A rate dislocation mean-reverts as capacity responds                   |
| `weather`      | 0.30   | Fast-reverting by nature                                               |
| unmapped       | 0.60   | `_DEFAULT_PERSISTENCE`                                                 |

Every factor `≤ 1.0`, so persistence only ever **discounts** the term — it
cannot inflate conviction. The raw mean fragility and the persistence-weighted
term are both surfaced in `supporting_signals` for auditability.

**Final score.** A Neutral idea has its conviction capped at 0.21 so it can
never outrank a genuine directional call. Bands (`_CONVICTION_BANDS`,
high-to-low):

| Score range      | Label    |
|------------------|----------|
| `[0.66, 1.00]`   | High     |
| `[0.42, 0.66)`   | Moderate |
| `[0.22, 0.42)`   | Low      |
| `[0.00, 0.22)`   | Watch    |

**Output.** `EquityIdea` carries `ticker`, `company_name`, `direction`,
`conviction_score`, `conviction_label`, plain-language `thesis`, full
`cascade_chain` (every `CascadeLink` decomposable to its three factors),
`driving_routes`, `driving_commodities`, `supporting_signals` (every conviction
term named with its weight and value, plus the selected weight-set name and
the cargo-mix source), `risk_flags` (every caveat — uniform-fallback in use,
fuel-cost drag, missing ETF confirmation, company-specific risks), `price`,
`change_30d`, `generated_at`, `conviction_weight_set`, and
`dominant_driver_key`.

**Framing.** Every idea is **modeled, rule-based and transparent** — framed
strictly as Bullish / Bearish / Neutral with a rationale, never a Buy/Sell
call and never a price target. The `EquityIdea.thesis` text repeats the
"modeled idea, not investment advice" phrase verbatim, and the Equity Signals
tab carries an unconditional "modeled — not investment advice" banner above
the fold. There is no model to train, no hidden weighting, and no fitted ML
anywhere in the cascade.

---

## Signal validation

### `signal_validation` — hit-rate scorecard vs equal-weight baseline

**Function.** `validate_signals(equity_ideas, commodity_signals, stock_data,
*, forward_days=21, sample_stride=5) -> ValidationReport`. Convenience wrapper
that builds the live signals first:
`build_validation_report(stress_report, exposure_matrix, stock_data, *,
insights=None, forward_days=21) -> ValidationReport`.

**The gap this closes.** The platform's older `processing/backtester.py`
historically exercised only ~8 hard-coded heuristic signals (BDI momentum,
z-score reversion, calendar triggers). The signals the platform *actually*
surfaces to a user — the cascade's `EquityIdea` list and the
`CommodityShippingSignal` list — were never validated. This module fills
that gap with a transparent hit-rate scorecard against forward synthetic
returns.

**Why not a faithful historical re-run.** A fully faithful replay would
reconstruct the SSI and the exposure matrix at every past date and re-score
the cascade day-by-day. That is infeasible from a single price snapshot
(the SSI needs contemporaneous port / route / macro state that is not
stored historically). The validator instead takes the honest path: each
*current* signal's directional claim is measured against forward returns
over the price history the platform does have.

**Methodology.**

1. **Sample forward returns** (`_forward_returns`). For each signalled
   ticker's close-price series, sample at every `_SAMPLE_STRIDE = 5`-th row
   that still has `forward_days` of history ahead, recording
   `(price[t + h] − price[t]) / price[t]`. Sampling weekly rather than daily
   stops overlapping forward windows from over-counting a single price swing.
2. **Score the directional claim** (`_hit_stats`).
   * Bullish hits when the forward return clears `+_FLAT_BAND` (0.005).
   * Bearish hits when it clears `-_FLAT_BAND` to the downside.
   * Neutral hits when the move stays *inside* `±_FLAT_BAND` — a Neutral call
     genuinely predicts "no decisive move", so a flat tape is correct.
   * `directional_return` is the mean return *in the signal's favour*
     (raw mean for Bullish, sign-flipped for Bearish, mean absolute distance
     from flat for Neutral).
3. **Build an equal-weight baseline** (`_baseline_pool` + `_baseline_hit_rate`).
   Pool every forward return across every ticker with usable history — that
   pool *is* the equal-weight, always-long baseline. For a Bullish signal the
   baseline is the fraction of pooled windows that rose; for a Bearish signal,
   the fraction that fell; for Neutral, the fraction that stayed flat. Keeps
   `edge_vs_baseline` apples-to-apples — "did the signal beat doing nothing?"
4. **Aggregate by conviction tier** (`_aggregate_tier`). Roll the validations
   up by `conviction_label` — `High`, `Moderate`, `Low`, `Watch` for cascade
   ideas; `Commodity` (`_COMMODITY_TIER`) for commodity signals. The tier hit
   rate is **observation-weighted**, so a signal backed by 40 windows counts
   more than one backed by 8. The tier ordering (`_tier_sort_key`) puts the
   cascade tiers in strength order, `Commodity` after.

**Output.** `ValidationReport` carries:

* `signals: list[SignalValidation]` — per-signal hit / miss counts
  (`n_observations`, `n_hits`, `hit_rate`, `avg_forward_return`,
  `directional_return`), the matched `baseline_hit_rate`, the derived
  `edge_vs_baseline`, a `low_sample` flag (`n_observations <
  _MIN_OBSERVATIONS = 8`), and a plain-language `note`.
* `tiers: list[TierScore]` — the per-tier hit rate vs baseline.
* Headline numbers (`overall_hit_rate`, `overall_baseline_hit_rate`,
  `overall_edge`, `overall_directional_return`), counts
  (`n_signals_validated`, `n_signals_skipped`), the horizon
  (`forward_days`), the longest synthetic history actually used
  (`price_history_days`), a one-line `summary`, and a `DataSource` stamped
  `kind=DataKind.MODELED`, `quality=DataQuality.DEMO` so the UI's
  provenance pill is honest about the synthetic basis.

**The whole result is arithmetic on observed forward returns.** There is no
fitted model, no learned weight, nothing to retrain. Every number traces to
a visible count of up-days vs down-days; every threshold (`_FLAT_BAND`,
`_MIN_OBSERVATIONS`, `_SAMPLE_STRIDE`, `_DEFAULT_FORWARD_DAYS`) is an
explicit, published constant. Because the platform runs on synthetic /
modeled price history, the scorecard is explicitly a *signal-quality* check
on demo data — never investment advice, never a price target.

The Backtest tab (`ui/tab_backtest.py`) renders this scorecard *first*,
above the older heuristic backtest, via the `_render_real_signal_validation`
helper. It surfaces the headline KPIs, the per-tier hit-rate bar chart, and
the per-signal detail table — every row labelled with its `DataSource` pill.

---

## Notes on the operating mode

Every series the models consume on this platform is either synthetic (the
voyage fleet, modeled fleet-wide stress, simulated price paths) or modeled
(per-route congestion, per-port baselines, structural vulnerability scores).
The data-quality machinery in `data/quality.py` is the source of truth on
that: every chart, every table and every KPI block carries a provenance pill
showing whether its inputs are `LIVE`, `SCRAPED`, `MODELED` or `DEMO`. A
modeled source renders its pill in the purple `MODELED` tier; a synthetic /
demo source renders it in the red `DEMO` tier. Nothing in this document
changes that — the synthetic / demo operating mode is the intended mode, and
the equity layer is a transparent rule-based generator, not investment
advice.
