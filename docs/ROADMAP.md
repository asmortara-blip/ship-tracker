# Roadmap

Live index of the build-out across five phases. The source-of-truth plan is
[`how-else-to-buidl-happy-perlis.md`](../../.claude/plans/how-else-to-buidl-happy-perlis.md)
in `~/.claude/plans/` — this file is the checked-in summary.

## Phase 1 — Foundation (in progress)

Plumbing that unlocks every downstream track.

- [x] Design-system audit tool — `tools/styles_audit.py`
- [x] Consolidate design system into `ui/styles.py`
    - Added `live_data_badge`, `regime_pill`, `spark_cell`, `source_footer`
    - Folded `stat_counter`, `mini_sparkline`, `gauge_ring`, `alert_banner`,
      `kpi_row`, `shipping_heat_bar`, `section_divider` in from `ui/components.py`
    - `ui/components.py` is now a deprecated re-export shim
- [x] Data-quality primitives — `data/quality.py` (`DataSource`, `DataSeries`)
- [x] Feed retrofits — FRED, Yahoo Finance, Freightos now have
      `*_wrapped(...)` variants returning `DataSeries`
- [x] Typed session schema — `state/session.py` (`SessionState`, `Filters`)
- [x] Tests scaffold — `tests/` with pytest config; unit tests for quality,
      session, styles additions, audit tool, and the refactored-tab smoke test
- [x] GitHub Actions CI — `.github/workflows/ci.yml` (ruff + pytest)
- [x] Migration playbook — [`TAB_MIGRATION.md`](TAB_MIGRATION.md)
- [x] Audit baseline — [`audit-baseline.csv`](audit-baseline.csv)

## Phase 2 — Refactor sweep + live data + first quant artifact

- [x] Palette consolidation sweep — 40 tabs now import from `ui.styles`
- [x] `engine/cointegration.py` surfaced inside `tab_indices`
      (see `_render_cointegration`)
- [x] Promote `tab_rate_analytics_refactored.py` to canonical
      `ui/tab_rate_analytics.py`
- [ ] Refactor wave 1 — remaining 14 unmigrated tabs (top-ROI:
      `tab_equipment`, `tab_results`, `tab_fleet`, `tab_emerging_routes`,
      `tab_port_demand`; see `audit-baseline.csv`)
- [ ] Refactor wave 2 — inline_divs / unsafe_html sweep on the 40 already
      palette-migrated tabs (total 1,332 inline divs / 199 unsafe_html
      remaining)
- [ ] Live-data map for `tab_indices` mocks (SCFI, WCI, HARPEX, FBX,
      BDTI/BCTI/BLNG/BLPG, FFA forward curve)
- [ ] Live-data map for `tab_news` entity mocks
- [ ] Live-data map for `tab_congestion` port-history mocks (AIS-derived)
- [ ] Delete `ui/components.py`

## Phase 3 — Analytical depth

- [x] `processing/congestion_rate_lag.py` — port-congestion → freight-rate lag model with walk-forward backtest; 19 tests
- [x] `engine/fleet_utilization.py` — 4-component composite utilization score (active share + capacity lock-in + delay intensity + forward congestion) with walk-forward backtest; 16 tests
- [ ] `processing/port_demand_forecaster.py` (upgrade with backtest)
- [ ] `engine/carrier_factor_model.py`
- [x] `state/scenarios.py` + scenario overlay mixin — `Scenario`/`ScenarioShock` schema with `<namespace>:<id>.<field>` target keys (wildcard `<id>` supported), 6-scenario canonical catalog, `overlay_value/multiplier/addend/iterable` apply helpers; 26 tests
- [x] `engine/portfolio_optimizer.py` — max_sharpe / min_variance / mean_variance / risk_parity via scipy SLSQP, long-only + weight-cap constraints, walk-forward backtest; 23 tests
- [x] Alert-rule editor UI on top of `engine/alert_engine_v2.py` — `save_rules`/`load_rules`/`reset_rules` persistence to `cache/alerts/rules.json` with project-root anchor; full per-rule editor (name, metric, threshold, condition, severity, email, enabled) + delete + Save All to Disk + Reset to Defaults; 11 persistence tests
- [x] `engine/narration_engine.py` wired to Claude API (cached daily) — `DailyNarration` + `NarrationContext` + `generate_daily_narration` appended alongside the existing rule-based functions; Haiku 4.5 default, day-keyed file cache at `cache/narrations/`, template fallback when key absent or call fails; 23 tests (all hermetic via mocked SDK)

## Phase 4 — New analytical tabs

- [ ] `tab_convergence.py` — Convergence & Divergence Lab
- [ ] `tab_nowcast.py` — Trade Nowcast
- [ ] `tab_idea_engine.py` — Signal-to-Trade Ideas
- [ ] `tab_risk_lab.py` — Risk Lab
- [ ] `tab_briefing.py` — Daily Briefing (LLM-narrated)
- [ ] Cross-tab filter bar (reads from `state/session.py`)
- [ ] "Export this view" PDF on every tab

## Phase 5 — Harden and ship

- [ ] Coverage: engine 80%, processing 70%, data 60%
- [ ] Data-SLA dashboard in `tab_data_health`
- [ ] State layer — SQLite via `database/schema.sql`
- [ ] Deployment — Streamlit Community Cloud + Fly.io `Dockerfile`
- [ ] Observability — log rotation + in-app log viewer + basic metrics

## Guiding principles

1. Analytical depth is the product; refactor serves depth.
2. One canonical design system — `ui/styles.py`.
3. Every figure surfaces its data source via `live_data_badge(...)`.
4. Cached-first, async-tolerant; never block the UI on a cold API.
5. Every new model ships with a walk-forward backtest.
