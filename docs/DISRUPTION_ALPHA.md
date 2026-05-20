# Disruption Alpha

Disruption Alpha is one of the ten navigation sections of the ShipTracker
intelligence platform. Where most sections answer a single question — *how
congested is this port?*, *where are freight rates?* — Disruption Alpha is an
**end-to-end pipeline**. It runs a disruption signal all the way from raw
vessel movement to a ranked, fully-traceable list of candidate equity ideas.

This document explains the feature the way the codebase does: the code is the
source of truth, and every claim below is grounded in the modules listed in
[Modules](#the-five-modules).

## Purpose

The pipeline has five stages, each a tab and each backed by a processing
module:

1. **Track vessel voyages.** Build a modeled fleet of in-transit voyages on
   real route geometry, so a *voyage* (origin, departure, progress, ETA)
   exists even though the underlying AIS feed only reports position.
2. **Detect and forecast shipping disruption.** Fuse chokepoint, congestion,
   weather, rate and structural-vulnerability signals into one fleet-wide
   Shipping Stress Index (SSI), resolved per route, and project it forward
   over 7- and 30-day horizons.
3. **Project disruption to a macro read.** Sit the disruption-first SSI next
   to the health-first Supply Chain Health Index (SCHI), explain the
   relationship, and surface the closest predefined scenario as a modeled
   projection of where the stress could carry rates and macro conditions.
4. **Link disrupted routes to the commodities they carry, and commodities to
   exposed companies.** A dense company↔commodity exposure matrix replaces the
   old sparse 4-entry map, so the chain *commodity move → exposed companies*
   can be walked without a black box.
5. **Surface ranked candidate equity ideas.** Walk the full chain — company →
   commodity → route → stress — to its conclusion: for every tracked shipping
   ticker, build a ranked equity idea where every number is decomposable into
   its visible factors.

The result is a chain that is traceable end to end. A user can start at a
single delayed vessel and follow the reasoning, hop by hop, to a Bullish or
Bearish read on a specific shipping equity — and inspect every intermediate
number along the way.

## The five tabs

Disruption Alpha registers five sub-pages, rendered as `st.tabs()` in the main
content area: **Voyage Tracker · Disruption Radar · Macro Projection · Supply
Linkage · Equity Signals**.

### 1. Voyage Tracker

*Source: `ui/tab_voyage_tracker.py`.*

Stage 1. Search any modeled voyage in the synthetic fleet, then inspect it: a
great-circle route map (origin and destination port markers, the lane line,
and the vessel's current position), a progress gauge, a nominal-vs-weather-
adjusted ETA, and a delay banner when the voyage is materially behind
schedule. A fleet KPI strip sits at the top (voyages tracked, on-schedule %,
delayed %, average delay, disrupted lanes); a full-fleet table sorted
most-delayed-first sits below. **What it's for:** giving the disruption signal
a physical, vessel-level anchor before any of it is aggregated.

### 2. Disruption Radar

*Source: `ui/tab_disruption_radar.py`.*

Stage 2. Detect and forecast what is disrupted across the fleet. The headline
is the fleet-wide Shipping Stress Index — a 0–1 composite with a status banner
and gauge — followed by the five-component breakdown (chokepoint, congestion,
weather, rate, vulnerability), a per-route stress heat bar, a per-route
disruption table sorted worst-first, and a 7/30-day stress forecast.
**What it's for:** turning a fleet of voyages into a single, decomposable read
on *how stressed the system is and which lanes are driving it*.

### 3. Macro Projection

*Source: `ui/tab_macro_projection.py`.*

Stage 3. Project fleet-wide disruption onto a macro read. Two composite
indices sit side by side: the disruption-first **SSI** (*what is breaking*) and
the health-first, six-dimensional **Supply Chain Health Index** (*the resulting
state of the system*). The tab never re-derives one from the other — it shows
both, maps the SSI's dominant stress drivers onto the SCHI dimensions they are
pushing, and surfaces the closest predefined `scenario_analyzer` scenario as a
modeled projection. **What it's for:** answering *if this disruption persists,
what does the macro picture look like?*

### 4. Supply Linkage

*Source: `ui/tab_supply_linkage.py`.*

Stage 4 — the "Link" stage. It walks the visible hops from a physical
disruption to a portfolio-relevant exposure: *disrupted lane → the commodity
that lane carries → the ETF that proxies that commodity's demand → the shipping
companies exposed to it*. The page shows a commodity KPI strip, a
disruption→commodity→company exposure table (one row per HS cargo category,
ordered by ETF-signal strength), a companies × commodities exposure heatmap,
and a per-route cargo-mix drill-down. **What it's for:** making the leap from
shipping physics to company exposure, with every figure tracing back to a
registry/profile constant or a tracked ETF's 30-day move.

### 5. Equity Signals

*Source: `ui/tab_equity_signals.py`.*

Stage 5, the final stage. It turns the linkage into ranked, **fully traceable**
candidate equity ideas. An unconditional "modeled — not investment advice"
banner sits above the fold. Below it: a consensus strip (net signal, counts,
top idea, average conviction) with a directional conviction-distribution rail,
then ranked `EquityIdea` cards — conviction-sorted, each with a numbered rank
chip and a traceable-detail expander. The expander reproduces the cascade chain
hop-by-hop as a table, plus driving routes, commodities, supporting signals and
risk flags. **What it's for:** delivering the pipeline's conclusion as
decision-support — a direction with a rationale, never a Buy/Sell call and
never a price target.

## The five modules

The five tabs are thin presentation layers. The analytical work lives in five
pure modules — no Streamlit imports, no `st.` calls — each tolerant of empty
inputs (it returns neutral defaults rather than raising, per the codebase
convention).

### `data/voyage_dataset.py` — Modeled Voyage Fleet

**Role.** The AIS feed reports vessel *position* and *destination* but has no
notion of an origin, a departure time, or voyage progress — so a *voyage* has
to be modeled. This module builds a deterministic fleet of in-transit (or
arrived) voyages, one batch per canonical route, using real origin/destination
port coordinates for great-circle geometry. The fleet is seeded from the
current UTC date, so it is stable within a session and refreshes day to day.
Delays are deliberately **biased toward genuinely disrupted lanes**: routes
touched by an active chokepoint disruption or a current-season weather alert
carry materially larger delays.

**Key dataclass.**

- `Voyage` — one modeled voyage. Fields: `voyage_id`, `vessel_name`, `mmsi`,
  `vessel_type`, `route_id`, `origin_locode`, `dest_locode`, `departed_at`,
  `nominal_transit_days`, `eta_nominal`, `eta_adjusted`, `progress_pct`,
  `current_lat`, `current_lon`, `status` (`"On Schedule"` | `"Minor Delay"` |
  `"Major Delay"` | `"Arrived"`), `delay_days`, `speed_kts`,
  `congestion_at_dest`, `weather_delay_days`, `chokepoints_on_route`.

**Provenance.** `VOYAGE_DATA_SOURCE: DataSource` — `DataSource.modeled("Modeled
Voyage Fleet")`.

**Public functions.**

```python
build_voyage_fleet(seed: int | None = None,
                   per_route: tuple[int, int] = (4, 9)) -> list[Voyage]
get_voyage(voyage_id: str, fleet: list[Voyage] | None = None) -> Voyage | None
search_voyages(query: str, fleet: list[Voyage] | None = None) -> list[Voyage]
voyage_fleet_summary(fleet: list[Voyage]) -> dict
```

`voyage_fleet_summary` returns the headline KPIs (`total`, `in_transit`,
`arrived`, `on_schedule`, `minor_delay`, `major_delay`, `delayed`,
`delayed_pct`, `avg_delay_days`, `avg_progress_pct`, `avg_speed_kts`,
`disrupted_routes`).

### `processing/shipping_stress_index.py` — Shipping Stress Index (SSI)

**Role.** Fuse the platform's siloed disruption datasets into a single
per-route `stress_score` and a fleet-wide `overall_ssi`. For every canonical
route the SSI blends five components — **chokepoint** (0.32), **congestion**
(0.22), **weather** (0.18), **rate** (0.18), **vulnerability** (0.10); the
weights live in `COMPONENT_WEIGHTS` and are asserted to sum to 1.0. The rate
component registers a *spike or a crash* as stress (both are disruption). The
overall SSI is a prominence-weighted average that double-weights the two
highest-volume global lanes (`transpacific_eb`, `asia_europe`). Scores are
banded **Calm / Elevated / Stressed / Severe**.

**Key dataclasses.**

- `RouteStress` — one route's stress breakdown: `route_id`, `route_name`,
  `stress_score`, the five component sub-scores (`chokepoint_stress`,
  `congestion_stress`, `weather_stress`, `rate_stress`, `vulnerability`),
  `dominant_driver` (human-readable top component), `affected_chokepoints`,
  `delayed_voyage_count`.
- `ShippingStressReport` — the fleet-wide report: `overall_ssi`, `ssi_label`,
  `ssi_color`, `route_stress` (list of `RouteStress`, sorted worst-first),
  `component_scores`, `top_disruptions`, `wow_change`, `data_timestamp`.

**Public function.**

```python
compute_shipping_stress(freight_data: dict, macro_data: dict,
                        port_results: list, route_results: list,
                        voyage_fleet=None) -> ShippingStressReport
```

When `voyage_fleet` is supplied, each `RouteStress` also reports how many of
its voyages are delayed.

### `processing/disruption_forecast.py` — Disruption Stress Forecaster

**Role.** Forecast shipping disruption *forward* over 7- and 30-day horizons.
Thin orchestration over forecasters that already exist — congestion trajectory
(`congestion_predictor`), freight-rate direction (`rate_forecaster` ML, with a
linear `forecaster` fallback), and the Monte Carlo P90 rate tail
(`monte_carlo`). The three signals are blended with persistence-heavy weights —
current 0.55, congestion 0.28, rate 0.17 — into 7-/30-day stress numbers on a
0–1 scale.

**Key dataclass.**

- `StressForecast` — a forward projection for one route: `route_id`,
  `route_name`, `current_stress`, `stress_7d`, `stress_30d`, `trend`
  (`"Improving"` | `"Stable"` | `"Worsening"`), `rate_forecast_pct`,
  `mc_p90_upside`, `narrative`, `drivers`.

**Public functions.**

```python
forecast_route_stress(route_id: str, freight_data: dict, macro_data: dict,
                      route_results: list,
                      current_stress: float | None = None) -> StressForecast
forecast_all_stress(freight_data: dict, macro_data: dict, route_results: list,
                    stress_report: Any | None = None) -> list[StressForecast]
```

`forecast_all_stress` returns one `StressForecast` per route, sorted by
`stress_30d` descending. When a `ShippingStressReport` is passed as
`stress_report`, its `route_stress` entries seed each route's `current_stress`;
the report is read duck-typed and is not imported, to avoid a circular import.

### `processing/exposure_matrix.py` — Company↔Commodity Exposure Matrix

**Role.** The "Link" layer. Every HS cargo category is mapped to a
representative tracked equity ETF (`COMMODITY_ETF_MAP` — e.g. electronics→XRT,
machinery→XLI, chemicals→XLB, agriculture→DBA, metals→DBB), and every tracked
shipping company is given a weight vector over those same categories
(`COMPANY_COMMODITY_EXPOSURE`), derived at import time from the company's trade
routes averaged through `cargo_analyzer.get_route_cargo_mix()`, tilted toward a
sector default and adjusted by a small table of hand-tuned overrides. USO
(bunker-fuel) is tracked separately as a *cost* overlay, not a demand proxy.

**Key dataclass.**

- `CommodityExposure` — one HS cargo category, its ETF proxy, and the companies
  exposed to it: `hs_category`, `category_label`, `etf_ticker`,
  `etf_price_change_30d`, `direction` (`"Bullish"` | `"Bearish"` |
  `"Neutral"`), `affected_routes`, `bullish_companies`, `bearish_companies`,
  `exposure_note`.

**Module-level derived constant.** `COMPANY_COMMODITY_EXPOSURE: dict[str,
dict[str, float]]` — `ticker → {hs_category: weight}`, each company's vector
summing to 1.0, built deterministically at import.

**Public functions.**

```python
routes_for_commodity(hs_category: str) -> list[str]
company_commodity_weights(ticker: str) -> dict[str, float]
build_exposure_matrix(stock_data: dict) -> list[CommodityExposure]
```

`build_exposure_matrix` returns one `CommodityExposure` per HS category; a
missing ETF degrades that category to a 0.0 move and `"Neutral"` direction
rather than raising.

### `processing/disruption_cascade.py` — Cascade Scorer + `EquityIdea`

**Role.** The "Conclude" stage. For every tracked shipping ticker the scorer
walks the chain — *exposure weight to a commodity → that commodity's routes →
each route's SSI stress → the route's cargo share of the commodity* — recording
one traceable hop per (commodity, route) pair, then resolves a direction and a
conviction score.

- **Direction is explicit, not learned.** `_DIRECTION_RULES` is a documented,
  auditable table keyed by `(company sector, dominant stress driver)` yielding
  a `(sign, rationale)` pair. Every sign in the output traces to one named row.
  A separate bearish fuel-cost overlay (`_FUEL_COST_RULE`) handles the USO
  channel: a rising USO can drag a bullish cascade to Neutral or push a neutral
  cascade Bearish, but never strengthens a bullish call.
- **Per-route cargo shares are real, not uniform.** A hop's `cargo_share` is
  the *actual* per-route commodity share from
  `cargo_analyzer.get_route_cargo_mix(route_id, {})`, so a lane that genuinely
  carries more of a commodity contributes proportionally more to the cascade.
  The uniform `1 / N` split survives only as a graceful fallback when no route
  yields a usable mix; when it triggers, the idea raises a risk flag stating
  that per-lane contributions are approximate.
- **Conviction is a transparent weighted sum** of four named, normalised terms
  — cascade magnitude, signal agreement, commodity-ETF confirmation and
  persistence-weighted route vulnerability. **The weights are not one fixed
  set.** `_CONVICTION_WEIGHT_SETS` holds five per-driver sets (`default`,
  `chokepoint`, `rate`, `weather`, `vulnerability`), each summing to 1.0 and
  asserted at import. A chokepoint-driven idea up-weights cascade magnitude
  (the physical reroute *is* the signal); a rate-dislocation idea up-weights
  signal agreement and ETF confirmation (a price move wants independent
  confirmation); a weather idea trims cascade and leans on corroboration; a
  vulnerability-driven idea up-weights the vulnerability term. Selection is a
  plain dictionary lookup on the dominant driver key, and the *name* of the
  set actually used is stated verbatim in `supporting_signals` — the score
  stays fully reproducible.
- **The vulnerability term is mean-reversion aware.** Raw mean route fragility
  is multiplied by a per-driver persistence factor (`_DRIVER_PERSISTENCE`):
  fast-reverting stress (weather, 0.30) is heavily discounted, slow-reverting
  structural stress (a chokepoint closure, 0.95; pure vulnerability, 1.00) is
  not. The factor only ever discounts — it can never inflate conviction — and
  both the raw fragility and the persistence-discounted term are surfaced in
  `supporting_signals` so the discount is auditable.
- **Every term is surfaced verbatim** in the idea's `supporting_signals`,
  alongside the named weight set used and the cargo-mix source
  (real-vs-fallback). Optional `insights` are cross-referenced for
  *corroboration only* — never required, never changes a direction.

**Key dataclasses.**

- `CascadeLink` — one traceable hop: `route_id`, `route_stress`, `hs_category`,
  `cargo_share`, `commodity_signal`, `contribution`
  (`= route_stress * cargo_share * exposure_weight` — fully decomposable).
- `EquityIdea` — a ranked, traceable candidate idea for one ticker: `ticker`,
  `company_name`, `direction` (`"Bullish"` | `"Bearish"` | `"Neutral"`),
  `conviction_score`, `conviction_label` (`"High"` | `"Moderate"` | `"Low"` |
  `"Watch"`), `thesis`, `cascade_chain` (list of `CascadeLink`),
  `driving_routes`, `driving_commodities`, `supporting_signals`, `risk_flags`,
  `price`, `change_30d`, `generated_at`.

**Public functions.**

```python
score_equity_ideas(stress_report, exposure_matrix, stock_data: dict,
                   insights=None) -> list[EquityIdea]
cascade_summary(ideas: list[EquityIdea]) -> dict
```

`score_equity_ideas` returns one `EquityIdea` per tracked shipping ticker,
sorted by `conviction_score` descending. `cascade_summary` rolls the list into
headline counts (`total`, `bullish_count`, `bearish_count`, `neutral_count`,
`net_signal`, `top_idea`, `top_ticker`, `avg_conviction`,
`high_conviction_count`).

## End-to-end data flow

A disruption propagates from a vessel to an equity idea like this:

```
build_voyage_fleet()                       voyage_dataset
        │   modeled voyages on real route geometry; delays biased
        │   toward chokepoint- and weather-disrupted lanes
        ▼
compute_shipping_stress(..., voyage_fleet)  shipping_stress_index
        │   per-route RouteStress + fleet-wide ShippingStressReport
        │   (chokepoint · congestion · weather · rate · vulnerability)
        ├──────────────► forecast_all_stress(stress_report)   disruption_forecast
        │                    7-/30-day StressForecast per route
        ▼
build_exposure_matrix(stock_data)           exposure_matrix
        │   HS category → ETF proxy → exposed companies
        │   COMPANY_COMMODITY_EXPOSURE: ticker → commodity-weight vector
        ▼
score_equity_ideas(stress_report,           disruption_cascade
                   exposure_matrix,
                   stock_data, insights)
        │   per ticker: walk commodity → route → stress hops,
        │   resolve direction via _DIRECTION_RULES, sum conviction
        ▼
ranked list[EquityIdea]   →  Equity Signals tab
```

### Worked example

Suppose a Suez/Bab-el-Mandeb-class chokepoint disruption is active.

1. **Voyage Tracker.** `build_voyage_fleet()` biases delays on every route the
   chokepoint touches. Asia–Europe voyages now show `Major Delay` status, a
   `+8`-to-`+12`-day `delay_days`, and the chokepoint name in
   `chokepoints_on_route`.

2. **Disruption Radar.** `compute_shipping_stress()` scores the `asia_europe`
   route. Its `chokepoint_stress` component is high; with the 0.32 chokepoint
   weight the route's `stress_score` lands in the *Stressed* band and its
   `dominant_driver` resolves to `"Chokepoint disruption"`. Because
   `asia_europe` is prominence-weighted ×2, the fleet-wide `overall_ssi` lifts
   noticeably. `forecast_all_stress()` projects the stress forward 7 and 30
   days.

3. **Macro Projection.** The high SSI is shown beside the SCHI; the chokepoint
   driver is mapped onto the SCHI dimensions it is pushing, and the closest
   `scenario_analyzer` scenario is surfaced as a modeled projection.

4. **Supply Linkage.** `build_exposure_matrix()` shows `asia_europe` carries
   finished manufactured goods — electronics, machinery, apparel. Those
   categories' demand-proxy ETFs (XRT, XLI) are read for confirmation.

5. **Equity Signals.** `score_equity_ideas()` walks the cascade for, say, ZIM.
   ZIM is a `container` carrier with a high `electronics` exposure weight;
   `electronics` implies routes including `asia_europe`; that route's
   `RouteStress.stress_score` is high. The hop is recorded as a `CascadeLink`
   with `contribution = route_stress × cargo_share × exposure_weight`. The
   `_DIRECTION_RULES` lookup for `("container", "chokepoint")` returns `+1` —
   *"Chokepoint reroutes strand container capacity and absorb tonne-miles,
   tightening box supply — price-positive for liners and lessors."* The
   conviction score sums the four named terms; if XRT confirms and a
   decision-engine insight names ZIM, agreement rises. The result is a
   **Bullish** `EquityIdea` for ZIM with a *High*/*Moderate* conviction label —
   and the Equity Signals expander reproduces every hop, term and caveat so the
   user can audit the entire path back to that one delayed Asia–Europe vessel.

If, at the same time, USO (bunker fuel) has risen more than 3% over 30 days,
the fuel-cost overlay engages: the published direction for ZIM may be dragged
from Bullish to **Neutral**, with the fuel-cost rationale added to
`risk_flags` — while the *cascade direction* stays Bullish so ETF/insight
corroboration is still measured honestly.

## Integration

### `ui/nav.py`

Disruption Alpha is the third entry in the `SECTIONS` list:

```python
{
    "key": "disruption_alpha",
    "icon": "📡",
    "label": "Disruption Alpha",
    "description": "Voyage tracking → disruption → equity ideas",
    "color": "#3572b0",
    "sub_pages": ["Voyage Tracker", "Disruption Radar", "Macro Projection",
                  "Supply Linkage", "Equity Signals"],
},
```

The sidebar renders it as a navigation button; selecting it sets
`st.session_state["nav_section"]` to `"disruption_alpha"`.

### `app.py`

`app.py` mirrors the same registration tuple (`("disruption_alpha", "📡",
"Disruption Alpha", "Voyage tracking → disruption → equity ideas")` and a
section-color entry). When `active_section == "disruption_alpha"`, it creates
five `st.tabs()` and dispatches each to its tab module's `render()`. Each call
is individually wrapped in `try/except`, so a failure in one tab surfaces an
`st.error(...)` without taking the section down:

| Tab              | Module                       | `render(...)` arguments |
|------------------|------------------------------|--------------------------|
| Voyage Tracker   | `ui.tab_voyage_tracker`      | `freight_data, route_results` |
| Disruption Radar | `ui.tab_disruption_radar`    | `freight_data, macro_data, port_results, route_results` |
| Macro Projection | `ui.tab_macro_projection`    | `port_results, freight_data, macro_data, route_results` |
| Supply Linkage   | `ui.tab_supply_linkage`      | `stock_data, freight_data, macro_data, port_results, route_results, trade_data` |
| Equity Signals   | `ui.tab_equity_signals`      | `stock_data, freight_data, macro_data, port_results, route_results, insights` |

All five `render()` functions end with `**kwargs` for call-site argument
safety, and each accepts `None`/empty inputs (the modeled fleet is
self-contained, and the processing modules degrade to neutral defaults).

## Important framing

**All data in Disruption Alpha is modeled or synthetic.** The voyage fleet is
synthetic (real route geometry, modeled voyages); the Shipping Stress Index,
the forecast, the macro projection and the exposure matrix are all computed
composites, not directly observed series. Provenance is labeled honestly
throughout via `data/quality.py`: every tab carries a `MODELED` badge and a
`source_footer(...)` built from `DataSource.modeled(...)`. The
`DataSource`/`DataSeries` machinery exists precisely so the UI can tell the
user how much to trust each number — a modeled source renders its provenance
pill in the `MODELED` (purple) tier, a synthetic/demo source in the `DEMO`
(red) tier.

**The equity layer is decision-support, not investment advice.** Equity
Signals is a transparent, rule-based, fully-traceable system:

- **Transparent.** Direction comes from the explicit, documented
  `_DIRECTION_RULES` table — there is no model to train and no hidden
  weighting. Conviction is a plain weighted sum of four named terms drawn
  from a per-driver weight set in `_CONVICTION_WEIGHT_SETS`; every set is
  published, every set is asserted to sum to 1.0, and the *name* of the set
  in use for each idea is stated in `supporting_signals`. The vulnerability
  term is multiplied by an explicit per-driver persistence factor
  (`_DRIVER_PERSISTENCE`) — also published and only ever a discount.
- **Traceable.** Every `EquityIdea` reproduces its full reasoning path: the
  cascade chain hop-by-hop (each `CascadeLink.contribution` decomposes into
  `route_stress × cargo_share × exposure_weight`, where `cargo_share` is the
  real per-route share with a documented uniform fallback), every conviction
  term in `supporting_signals`, and every caveat in `risk_flags`.
- **Framed as a view, not a trade.** Ideas are strictly **Bullish / Bearish /
  Neutral with a rationale** — never a Buy/Sell call and never a price target.
  The Equity Signals tab carries an unconditional "modeled — not investment
  advice" banner above the fold, and the `EquityIdea` thesis text repeats the
  "modeled idea, not investment advice" framing.

Disruption Alpha is a research and decision-support tool. Nothing it produces
should be read as a recommendation to buy or sell any security.

## Validation

The platform's older `processing/backtester.py` only ever exercised a handful
of hard-coded heuristic signals (BDI momentum, z-score reversion, calendar
triggers). The signals Disruption Alpha *actually* surfaces — the cascade's
ranked `EquityIdea` list and the `CommodityShippingSignal` list — were never
themselves validated. `processing/signal_validation.py` closes that gap.

`build_validation_report(...)` runs the real pipeline end-to-end
(`score_equity_ideas` + `analyze_commodity_signals`) and then, for every live
signal, replays its directional claim — Bullish / Bearish / Neutral — against
forward returns over the platform's synthetic price history. A signal "hits"
when the forward move clears the `_FLAT_BAND = 0.005` directional dead-band
in its favour (a Neutral call is correct when the move *stays inside* that
band — a flat tape genuinely is the Neutral prediction). The output is a
plain `ValidationReport` of per-signal hit /
miss counts and an **observation-weighted hit rate broken down by the
cascade's own conviction tiers** (High / Moderate / Low / Watch / Commodity),
each tier compared against an **equal-weight, always-long synthetic baseline**
pooled from every ticker's forward windows. A tier only earns its keep if its
hit rate beats that baseline.

The Backtest tab renders this scorecard *first* (above the older heuristic
backtest), with a per-tier hit-rate chart and a per-signal detail table. The
provenance pill is stamped `MODELED` / `DEMO`: the result is arithmetic on
synthetic forward returns, never investment advice. See `docs/MODELS.md` for
the full validator methodology.
