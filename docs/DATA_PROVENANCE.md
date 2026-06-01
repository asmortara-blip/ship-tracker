# Data Provenance — what is real, what is modeled

> **Bottom line, stated plainly:** Do **not** trade real money on this platform as
> built. The genuinely live data is limited to keyless **equity prices**, **FX**,
> **World Bank macro**, and **RSS news headlines**. The *differentiated* shipping
> signals this platform is about — freight rates, port congestion, live AIS vessel
> positions, canal wait-times — are **synthetic models** dressed in real-looking
> schema. The disruption core (voyages, chokepoint states, vulnerability) is
> **hardcoded/modeled**, and **no real company fundamentals reach the analytics**.
> Every headline signal (SSI → cascade → equity ideas → world-graph criticality) is
> a deterministic function of that modeled core. Treat the whole derived-signal stack
> as **research-grade / illustrative**, not actionable. This is not investment advice.

This document is the honest map produced by a provenance audit (raw feeds + derived
signals). It is the reference for the data-realness question: *"are we getting real
data we could make informed trading decisions on?"* — the answer is mostly **no**, and
this explains exactly where the line is.

---

## How provenance labelling works (and where it's bypassed)

`data/quality.py` defines `DataSource(kind, quality, ...)` with constructors
`.live()`, `.cached()`, `.scraped()` (→ *unofficial*), `.modeled()` (→ *modeled*),
`.demo()`. The infrastructure exists.

**Caveat — the labels are mostly not attached on the live code paths.** Only the
`*_wrapped()` feed variants and `fetch_baltic_daily` actually emit a `DataSource`.
The primary functions the UI/engine call (`fetch_macro_series`, `fetch_all_stocks`,
`fetch_fbx_rates`, `fetch_vessel_counts`, `fetch_all_ports`, …) return bare
`dict`/`DataFrame` with **no provenance object**. Synthetic rows are distinguishable
only by an in-band `source` string column (`"synthetic"`, `"wb_synthetic"`,
`"synthetic_baseline"`). So the "provenance pill" exists but the hot paths bypass it.

There is **no global DEMO_MODE switch and no "use real data" toggle.** Per-feed
presence-of-API-key is the only control.

---

## Raw feeds (17 modules)

Default state below = **this machine with no API keys configured** (no `.env`, no
`.streamlit/secrets.toml` — only `*.example` templates; the only feed-relevant env var
present in the shell is `ALPHA_VANTAGE_KEY`).

| Feed | Real source | Synthetic fallback (trigger) | Default here | To make real |
|---|---|---|---|---|
| **stock_feed** | Yahoo Finance (`yfinance`, no key) | none — failed tickers dropped | **REAL** | already real; needs network |
| **fred_feed** | FRED (`FRED_API_KEY`) | **none** — returns empty if no key | **EMPTY** (no BDI/yields/oil/CPI/PMI) | set `FRED_API_KEY` (free) |
| **freight_scraper / FBX** | Freightos public JSON + HTML | **YES** — mean-reverting OU random walk (API ~always empty) | **SYNTHETIC** | real FBX needs a Freightos licence |
| **freight_scraper / Baltic** | Baltic Exchange HTML (paywalled) | **YES** — bundled `baltic_backfill.csv` fixture | **SYNTHETIC/STALE** | Baltic Exchange paid licence |
| **comtrade_feed (WITS)** | World Bank WITS / WB merchandise | **YES** — real totals split by **hardcoded sector shares** | **SYNTHETIC product-mix** | UN Comtrade key/account |
| **ais_feed (PortWatch)** | IMF PortWatch REST | **YES** — hardcoded 2024 baselines × seasonal × sin-wobble | **SYNTHETIC** | PortWatch ships ArcGIS/CSV, not this REST shape |
| **aisstream_feed** | AISstream.io (`AISSTREAM_KEY`) | **YES** — fabricated names/MMSI/positions from route geometry | **SYNTHETIC** | set key **and** switch transport to WebSocket |
| **worldbank_feed** | World Bank v2 (no key) | none — empty on failure | **REAL** (1–2yr lag) | already real |
| **news/sentiment** | 6 free RSS feeds (no key) | none — empty list if all dead | **REAL** | already real |
| **newsapi_feed** | NewsAPI (`NEWS_API_KEY`) — optional augment | none | **INACTIVE** (no key) | set `NEWS_API_KEY` (free 100/day) |
| **alphavantage_feed** | Alpha Vantage (`ALPHA_VANTAGE_KEY`) | none | **AMBIGUOUS** — key in shell but cache empty; only real if the running process inherits the env var | confirm env reaches process |
| **imf_feed** | IMF SDMX (no key) | none — neutral 0.5 defaults downstream | **REAL if reachable** (unproven; IMF SDMX flaky) | network only |
| **oecd_feed** | OECD SDMX (no key) | none — neutral defaults | **LIKELY EMPTY** — `stats.oecd.org/SDMX-JSON` was decommissioned | rewrite to `sdmx.oecd.org` 2.1 |
| **currency_feed** | Yahoo FX (`yfinance`) | **YES** — hardcoded `_DEFAULTS` per pair | **REAL** (silently substitutes stale rate per gap) | already real |
| **canal_feed** | Panama ACP + Suez SCA HTML scrape | **YES** — hardcoded "plausible" constants (pages are JS-rendered → scrape ~always fails) | **SYNTHETIC** | paid maritime data provider |
| **carrier_intelligence** | none (curated by design) | n/a — hardcoded Q1-2026 fixtures | **MANUAL/SYNTHETIC** | Alphaliner/carrier-financials licence |
| **voyage_dataset** | none ("synthetic voyages on real route geometry") | n/a — fully modeled, **honestly labelled** | **MODELED** (correctly labelled) | real AIS voyage reconstruction |
| **historical_events** | static registry of real past events | n/a | **MANUAL (real, factual)** | intentional |

**Real by default here: ~4 of 17** — stocks, FX, World Bank macro, RSS news. Everything
that makes this platform *distinctive* is modeled.

---

## Derived signals — every one is a function of the modeled core

| Signal | Inputs (real / modeled) | Output | Trade-grade? | Not-advice label |
|---|---|---|---|---|
| **voyage fleet** (`build_voyage_fleet`) | Real route geometry; **modeled** vessels/positions/delays/congestion (RNG seeded off date) | MODELED | **No** | self-labels `DataSource.modeled` ✓ |
| **SSI** (`shipping_stress_index`) | All 6 components trace to modeled/hardcoded; rate axis (FBX/FRED) is the one real-ish input | MODELED | **No** | UI carries "MODELED" |
| **disruption cascade + EquityIdea** | Modeled stress + hardcoded exposure; **real price** (yfinance); rule-based direction/conviction | MODELED, rule-based; direction-only (no price target) | **No** (thesis rests on modeled stress) | **YES, explicit + repeated** ✓ |
| **alpha_engine** | Real prices + FRED + FBX, **but** `_fallback_price` injects synthetic; emits explicit **entry/target/stop + LONG/SHORT + Sharpe** | MODELED rule signals with hard price targets | **No** | **see note** — disclaimer added (this pass) |
| **exposure_matrix** | Modeled company→commodity weights (hardcoded profiles + overrides); real ETF 30d move | MODELED linkage + real overlay | linkage illustrative | weights documented "illustrative" |
| **company_profiler** | **Hardcoded** profiles (fleet, sector, employees, edge/risk text); real price technicals | MIXED | technicals real; "fundamentals" framing is hardcoded text | "fundamental analysis" framing is overstated |
| **world_graph + metrics + criticality** | Assembled from all of the above | MODELED (graph math sound, inputs synthetic) | **No** | inherits upstream provenance |

---

## The fundamentals gap (this gates any DCF / valuation)

**Real fundamentals exist in the codebase but do not reach the analytics, and are
off by default.**

- `data/alphavantage_feed.py` *can* fetch real revenue / margins / EBITDA / P/E /
  market cap (OVERVIEW + INCOME_STATEMENT) — but it's (a) gated on `ALPHA_VANTAGE_KEY`,
  and (b) consumed **only** by `ui/tab_data_health.py` + `state/db.py` (display/cache),
  **never** by `company_profiler`, `exposure_matrix`, `disruption_cascade`, or
  `alpha_engine`.
- `data/carrier_intelligence.py` has revenue but it's **hardcoded** ("updated Q1 2026").

There is **no FCF, capex, debt, or shares-outstanding time series anywhere** in the
analytics layer. **A DCF built here is illustrative** — it hardcodes assumptions or
reads the unwired, key-gated AV feed. Any valuation suite must say so on its face.

---

## What real data would change the verdict

1. **Real AIS voyage reconstruction** (origin/departure/progress) to replace
   `build_voyage_fleet`.
2. **Live chokepoint/disruption feed** (real Suez/Panama transit + conflict/closure
   status) to replace the hardcoded registry that gates all of SSI.
3. **Real port congestion** (berth/dwell/queue telemetry) to replace the demo smoother.
4. **Wired-in fundamentals** (AV feed actually consumed by the valuation path, with a
   key) — mandatory before any DCF is more than illustrative.
5. **Out-of-sample, walk-forward validation against realized stock returns.** The
   existing backtests validate the analytics against *each other*, not against P&L.

---

## Known config bugs (fixed in this pass)

- `.env.example` listed the **wrong key names** — `ALPHA_VANTAGE_API_KEY` and
  `NEWSAPI_KEY`, while the code reads `ALPHA_VANTAGE_KEY` and `NEWS_API_KEY`. A user
  filling in `.env.example` verbatim would silently get synthetic/empty for both.
  (`secrets.toml.example` was already correct.)

---

*Generated from a read-only provenance audit. Keep this honest: if a feed is wired to
real data later, update its row — and never present a modeled signal as a measurement.*
