# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
tags milestones as they complete each phase of [`docs/ROADMAP.md`](docs/ROADMAP.md).

## [0.1.0-phase1] - 2026-04-22

Phase 1 — foundation plumbing that unlocks every downstream track.

### Added

- **Design system** — `ui/styles.py` absorbs every palette constant and
  helper that was previously duplicated across tab files. New helpers:
  `live_data_badge`, `regime_pill`, `spark_cell`, `source_footer`,
  `page_header`, `metric_card_row`, `insight_card_html`, `status_badge`,
  `nav_section_button`.
- **Data-quality primitives** — `data/quality.py` introduces `DataSource`
  (live / scraped / modeled / demo) and `DataSeries` so every figure can
  surface where its numbers came from.
- **Feed retrofits** — `data/fred_feed.py`, `data/stock_feed.py`, and
  `data/freight_scraper.py` now ship `*_wrapped(...)` variants returning
  `DataSeries` instead of raw DataFrames. `data/fixtures/baltic_backfill.csv`
  is the offline fallback for Baltic series.
- **Typed session state** — `state/session.py` (`SessionState`, `Filters`).
- **Analytical engines** — `engine/cointegration.py` (Johansen + ECM +
  half-life) and `engine/carrier_factor_model.py` (factor frame → residual
  z-scores + signal backtest).
- **Test scaffold** — `tests/` with 66 unit tests across 7 files;
  `pytest.ini` configured with `--strict-markers` and warning filters.
- **CI workflow** — `.github/workflows/ci.yml` runs ruff + pytest on every
  push.
- **Release automation** — `.github/workflows/release.yml` turns any
  pushed `v*` tag into a GitHub Release with notes extracted from this file.
- **Audit tool** — `tools/styles_audit.py` + `docs/audit-baseline.csv` give
  a repeatable ROI-scored priority list for the Phase-2 refactor wave.
- **Migration playbook** — `docs/TAB_MIGRATION.md` (10-step recipe) and
  `docs/ROADMAP.md` (checked-in summary of the 5-phase plan).
- **Reference refactored tab** — `ui/tab_rate_analytics_refactored.py` is
  the target aesthetic every other tab should converge toward.

### Changed

- **40 tabs migrated** to the shared design system. Local palette redeclarations
  removed; `page_header`, `metric_card_row`, `badge`/`regime_pill`,
  `section_header`, `apply_dark_layout`, and `wsj_market_table` now come from
  `ui.styles`. Net ~1,900 lines of boilerplate removed with no visual
  regression.

### Deprecated

- `ui/components.py` is now a thin re-export shim. It will be deleted after
  the Phase-2 refactor wave finishes.

[0.1.0-phase1]: https://github.com/asmortara-blip/ship-tracker/releases/tag/v0.1.0-phase1
