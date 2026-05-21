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
- [x] `processing/port_demand_forecaster.py` (upgrade with backtest) — added `naive_history_forecast` (drift + trailing-mean baseline), `walk_forward_backtest` (MAE / RMSE / direction hit rate / bias), `backtest_all_ports` (multi-port batch sorted by MAE asc), `PortDemandBacktestResult` dataclass; 16 tests including flat-series MAE=0 / trend direction-hit ≥ 0.95 / drift_weight beats mean_weight on linear trend / sorted by MAE checks
- [x] `engine/carrier_factor_model.py` — added `attribute_window_return` + `attribute_all_carriers` + `FactorAttribution` dataclass for "why is ZIM up 8% this week?" decomposition (α + Σ β·f + residual; exact by construction); 9 attribution tests including decomposition-is-exact, factor contribution signs match β × ΣF, sorted-by-observed-return, missing-ticker safe. Model + tests + UI wiring (tab_portfolio) + walk-forward backtest were all already in place; this commit closes the analytical gap with factor attribution.
- [x] `state/scenarios.py` + scenario overlay mixin — `Scenario`/`ScenarioShock` schema with `<namespace>:<id>.<field>` target keys (wildcard `<id>` supported), 6-scenario canonical catalog, `overlay_value/multiplier/addend/iterable` apply helpers; 26 tests
- [x] `engine/portfolio_optimizer.py` — max_sharpe / min_variance / mean_variance / risk_parity via scipy SLSQP, long-only + weight-cap constraints, walk-forward backtest; 23 tests
- [x] Alert-rule editor UI on top of `engine/alert_engine_v2.py` — `save_rules`/`load_rules`/`reset_rules` persistence to `cache/alerts/rules.json` with project-root anchor; full per-rule editor (name, metric, threshold, condition, severity, email, enabled) + delete + Save All to Disk + Reset to Defaults; 11 persistence tests
- [x] `engine/narration_engine.py` wired to Claude API (cached daily) — `DailyNarration` + `NarrationContext` + `generate_daily_narration` appended alongside the existing rule-based functions; Haiku 4.5 default, day-keyed file cache at `cache/narrations/`, template fallback when key absent or call fails; 23 tests (all hermetic via mocked SDK)

## Phase 4 — New analytical tabs

- [x] `tab_convergence.py` — Convergence & Divergence Lab (added as 12th tab in Markets section). Built on new `processing/convergence_analyzer.py` (23 tests) — pairwise rolling-correlation analysis with Converging/Diverging/Decoupling/Stable classification. Tab: interactive window sliders, 3-card hero (top per direction), ranked table sorted by |Δr|, long-window correlation heatmap.
- [x] `tab_nowcast.py` — Trade Nowcast (added as 9th tab in Trade & Macro section). UI for the existing `processing/leading_indicators.py` machinery — composite score + 4-week forecast + recession probability headline strip, per-indicator detail table (15+ FRED series, sortable), weighted-contribution horizontal bar chart, lead-lag correlation heatmap of indicators against BDI at multiple lags. No new model code (the analytical pieces were all already in `processing/`); pure UI synthesis.
- [x] `tab_idea_engine.py` — Signal-to-Trade Ideas (wired as 6th tab in Disruption Alpha section). Synthesizes disruption_cascade ideas + scenario overlay (sidebar-controlled) + portfolio_optimizer mini. Hero / ranked table with Δ vs scenario / per-idea cascade rationale expanders / max-Sharpe weights on top bullish names.
- [x] `tab_risk_lab.py` — Risk Lab (added as 2nd tab in Risk section, after Risk Matrix). Built on new `processing/risk_lab.py` (21 tests): VaR/CVaR (historical + parametric) with horizon scaling, scenario stress test against the canonical catalog, market regime detector (Bull/Bear/Sideways/Crisis from ann_return + vol_ratio + drawdown). Tab: portfolio-value/confidence controls, 4-card VaR strip, sorted stress-test table + bar chart, big regime card with driving indicators.
- [x] `tab_briefing.py` — Daily Briefing (LLM-narrated) (added as 2nd tab in Dashboard section). Editorial-typography surface for engine.narration_engine: serif headline, multi-paragraph body, 3-col sections grid, transparency panel showing the SSI + top forecasts + indicators the LLM saw, force-refresh button that bypasses the day cache, source meta (model / tokens / generated_at).
- [x] Cross-tab filter bar (reads from `state/session.py`) — `ui/filter_bar.py` renders 5-column horizontal strip (date range, universe, routes, regions, demo-mode toggle) at the top of every section. Filters persist through `SessionState.filters`. Pure-function `apply_filters_to_freight` / `apply_filters_to_stock` helpers in `state/session.py` (10 new tests, hermetic). Demo wiring in `tab_rate_analytics` shows the route + date narrowing in action; any other tab can opt in via `ui.filter_bar.active_filters()`.
- [x] "Export this view" PDF on every tab — `utils/view_export.py` provides a generic `ViewSnapshot` / `ViewSection` / `ViewTable` schema and `build_view_pdf()` that returns PDF bytes for `st.download_button`. Any tab can build a snapshot from its already-computed content. Demo wired in `tab_briefing` (export button next to the refresh button, includes headline + body + section bullets + the transparency "Today's Inputs" table). 13 hermetic tests covering schema, UTF-8 sanitizer, multi-page paginate-safe output, content integrity.

## Phase 5 — Harden and ship

- [ ] Coverage: engine 80%, processing 70%, data 60%
- [ ] Data-SLA dashboard in `tab_data_health`
- [ ] State layer — SQLite via `database/schema.sql`
- [x] Deployment — Streamlit Community Cloud + Fly.io `Dockerfile` — single-stage Python 3.11-slim image, non-root user (UID 10001), Streamlit healthcheck against `/_stcore/health`, layered for cache-stable rebuilds. `.dockerignore` keeps cache/logs/tests/secrets out of the image. `docs/DEPLOYMENT.md` covers 3 paths (Streamlit Cloud / Docker / Fly.io) with required-env-vars table and volume-mount notes.
- [ ] Observability — log rotation + in-app log viewer + basic metrics

## Guiding principles

1. Analytical depth is the product; refactor serves depth.
2. One canonical design system — `ui/styles.py`.
3. Every figure surfaces its data source via `live_data_badge(...)`.
4. Cached-first, async-tolerant; never block the UI on a cold API.
5. Every new model ships with a walk-forward backtest.
