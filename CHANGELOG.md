# Changelog

_Generated 2026-06-01 from the git log — covers 456 commits. Conventional-commit prefixes (`feat:`, `fix:`, `ui:`, `engine:`, …) bucket entries into categories._

**DO NOT EDIT MANUALLY** — regenerate with `python -m tools.changelog_cli`.

## 2026-05-31

### ✨ Features

- **feat** audit failed login attempts (auth #9) (`6cd7207`)
  - Successful logins/signups were already audited; failed credential attempts were not, so a security review couldn't see brute-force / credential-stuffing. Adds auth.audit.record_login_failure, called from the login form's invalid-credenti...

### 🐛 Fixes

- **fix** investor CSV concentration_hhi matches the alert's full-footprint HHI (`3287184`)
  - port_supply_csv recomputed the per-ticker concentration_hhi column locally over the top-N-CAPPED port_exposures, so after the #8 fix (which computes HHI over the FULL footprint and stores it on CompanyPortFootprint) the CSV column disagr...
- **fix** bind tab_alpha's stock_data by keyword so the engine path actually runs (`73f5d07`)
  - An adversarial self-review of this session's diff caught that the earlier Alpha-tab fix (b4da425) was a no-op. The only production caller — app.py — dispatched tab_alpha.render POSITIONALLY: _r(route_results, port_results, freight_data,...
- **fix** reject case-insensitively-colliding usernames at signup (auth #13, safe half) (`0dd469b`)
  - signup's duplicate pre-check was case-sensitive, so "Admin" could be registered alongside an existing "admin" — a forward impersonation/confusion vector. The pre-check is now `WHERE username = ? COLLATE NOCASE` (the username charset is A...
- **fix** capacity-demand persistence_rate divides by the window, not nonzero days (#4) (`8df3b33`)
  - persistence_rate divided the same-sign day count by the number of NONZERO days, but the module/function docstrings + the field comment (and a "7 of 10 days" example) all describe it as a fraction of the WINDOW. For a window with balanced...
- **fix** CARGO_FLOW_ANOMALY now alerts on the per-category-jump signal (#5) (`ce531d0`)
  - check_cargo_flow_anomaly_alerts gated solely on report.jsd < jsd_alert_threshold, so a route flagged anomalous purely via a single-category jump (surge/collapse past jump_threshold_pp) with calm overall JSD fired NO alert — half of what...
- **fix** SSI component breakdown now reconciles to the headline SSI (#8) (`bbfdc15`)
  - component_scores was a SIMPLE per-route average while overall_ssi prominence- weights stress_score, so the per-component breakdown (and ssi_attribution's pct_share) didn't decompose the displayed SSI. component_totals now accumulates eac...
- **fix** perf budgets reported/compared the median while calling it the "mean" (`0f2f88c`)
  - get_perf_summary emitted only count/median_ms/p95_ms/error_count — no mean — but perf_budgets.check_budgets read median_ms into observed_mean_s. So every PERF_BUDGET alert body rendered "mean Xs" that was actually the median, and the max...
- **fix** investor report signal-type breakdown used a phantom MACRO_OVERLAY key (`fbaaf09`)
  - signal_count_by_type was seeded (and the always-present loop + the 3 mock signals used) the key "MACRO_OVERLAY", but the alpha engine emits signal_type="MACRO" (its canonical _SIGNAL_TYPES). So a real macro signal incremented a NEW "MACR...
- **fix** 2 news-sentiment bugs — region substring false-positives + dedup keep-policy (`711aa4c`)
  - - _extract_regions matched region keywords by bare substring, so the two-letter tokens "us"/"eu" hit inside Russia, August, customs, surplus, status, Europe, neutral, Reuters, … — mistagging nearly every article's region and corrupting r...
- **fix** SSI congestion dict-path honored a real 0.0 reading as "unknown" (`b7df49e`)
  - _route_congestion_stress's dict branch coalesced the congestion reading with an `or`-chain, so a destination port legitimately reporting 0.0 (the calmest, genuinely-uncongested case) was treated as missing and fell through to the neutral...
- **fix** Alpha tab's engine-signal path was permanently dead (generate_all_signals arity) (`b4da425`)
  - ui/tab_alpha.py called generate_all_signals(stock_data) with a SINGLE arg, but the function required 5 positional args (stock/freight/macro/port/route) with no defaults — so every call raised TypeError, which the tab's debug-level try/ex...
- **fix** GC the cargo-mix + company-risk snapshot trees (were unbounded) (`78e7cae`)
  - Two daily snapshot trees added in the analytics series — cache/cargo_mix_history and cache/company_risk_history — had a save job wired into the scheduler but NO garbage-collection job, unlike the sibling port_supply_snapshots tree (which...
- **fix** harden /spillover-graph min_lift + backtests baseline IO (`0cea346`)
  - - api_server /spillover-graph: min_lift=nan/inf parsed as floats and slid past the `< 0` guard, then serialized to the invalid-JSON tokens NaN/Infinity AND defeated the lift filter (lift < nan always False → every edge survives; lift < i...
- **fix** 2 analytics metrics now reconcile to their contract (spillover support, concentration HHI) (`62c53c6`)
  - The analytics-feature bug-hunt (the 8 new modules + wire-ups) surfaced two metrics that violated their own documented contract:

### ✅ Tests

- **test** cover the COMPANY_CONCENTRATION alert wiring (was zero-coverage) (`c912e88`)
  - check_company_concentration_alerts had no test (the analytics hunt flagged this gap — the analytic underneath is well-tested, but the alert-engine wiring was not). Adds two: a high full-footprint HHI fires a CRITICAL COMPANY_CONCENTRATIO...

### 📦 Other

- **cleanup** drop the degenerate forecast sign_agreement metric (#9) (`5bcd45b`)
  - forecast_accuracy_tracker.summarize_accuracy computed sign_agreement as (predicted > 0) == (actual > 0), but the forecasts it scores are stress LEVELS in [0, 1] (always non-negative), so it trivially "agreed" ~100% of the time — a mislea...

## 2026-05-30

### 🐛 Fixes

- **fix** dead BDI MACRO_SHIFT alert + unbounded flap-crossing list (`cd9faca`)
  - - alert_engine: the macro-shift rule looked up the Baltic Dry Index under BDIY/BDI/bdi, but the cache stores it under the FRED series id BSXRLM — the key chain never hit, so the BDI macro-shift alert could never fire. Try BSXRLM first; k...
- **fix** 3 UI bugs from the ui-hunt — dead Contagion section, report history, stray None (`46e11f4`)
  - - tab_port_supply_lines: _render_spillover_graph used alert_banner without importing it (NameError) and called wsj_market_table with a positional DataFrame instead of (headers, rows) (TypeError) — every branch raised, so the Contagion /...
- **fix** HTML-escape caller-controlled text at 3 unsafe_allow_html sinks (`7f56126`)
  - The ui/+utils bug-hunt surfaced three XSS sinks where caller-controlled text reached st.markdown(..., unsafe_allow_html=True) raw:
- **fix** thread-local DB connections (#2, CRITICAL) + atomic fire_count (#6 remainder) (`b4c68c4`)
  - #2 — state/db.py shared ONE sqlite3.Connection across every caller (check_same_thread=False), but sqlite3.threadsafety==1 forbids sharing a Connection across threads, and Streamlit runs each session in its own ScriptRunner thread — undef...
- **fix** per-job cadence gates so the worker is safe to run frequently (#4) (`4fea40d`)
  - main() ran the full job list on every invocation, so the documented daily cron left the SLA-critical jobs late: an unacked CRITICAL escalated, and a transient delivery failure retried, up to ~24h after the fact (their docstrings promise...
- **fix** retry-queue atomic claim (#7) + atomic kv_state counters (#6) (`010a878`)
  - Two concurrency fixes from the subsystem bug-hunt, both via patterns already proven elsewhere in the codebase:
- **fix** 5 confirmed bug-hunt findings (telemetry, webhook DoS, observability, path leak, doc) (`8176139`)
  - From the same adversarial subsystem bug-hunt as the prior commit:
- **fix** scope save_alerts trim per-user + spare unacknowledged rows (CRITICAL data-loss) (`46f9890`)
  - save_alerts ended every call with a GLOBAL, unscoped trim to _MAX_STORED (500) rows with no acknowledged guard — so in a multi-user deployment one user's burst of >500 alerts silently evicted ANOTHER user's oldest rows, including unackno...

### 📚 Docs

- **docs** correct the SSI-attribution "sums to ssi_total" claim (#8, partial) (`5236514`)
  - processing/ssi_attribution.py's module + attribute_ssi docstrings claimed the component contributions "Sum to ssi_total" / "reconcile to the fleet-wide score". They don't: pct_share is normalized against the component-weighted blend (Σ w...

### ✅ Tests

- **test** update scheduler source-introspection tests for the #4 cadence-gate refactor (`70c34e1`)
  - The #4 commit (4fea40d) routed main()'s per-job try/except blocks through the shared _run_gated / _run_always helpers, so the literal call sites ("run_X_job()") and inline log strings ("X step failed") no longer appear in inspect.getsour...

### 📦 Other

- **security** scope webhook /ack + /ack-all + pagerduty acks to the resolved user (#9) (`733b7a9`)
  - The webhook ack surfaces called acknowledge_alert(id) / acknowledge_all() with NO user_id. Out-of-process the engine resolves user_id via the (absent) Streamlit session → '' → scope_filter_sql('') applies NO restriction. So a single hold...
- **security** enable-MFA proof-of-possession (#7) (`2256366`)
  - enable_mfa flipped mfa_enabled without verifying the user could actually produce a code, so a mis-scanned secret would lock the account out on the next (now-mandatory) login.

## 2026-05-29

### 🐛 Fixes

- **fix** auth — verify_totp fail-closed on empty secret, recovery-code single-use rowcount (`e102778`)
  - Two confirmed security bugs from the auth/ bug-hunt (the clean ones; the rest are surfaced for prioritization).
- **fix** make the auto-disable breaker channel-level (fixes UI counter-scope mismatch) (`b9f73aa`)
  - The consecutive-failure counter + auto-disabled flag were keyed by (alert-owner user_id, channel_id): the delivery path writes under the ALERT owner (often '' for scheduler alerts) while the UI reads/resets under the logged-in operator —...
- **fix** alert_delivery correctness — no-retry-on-4xx, atomic counters, threshold normalize (`f0cbd82`)
  - Three correctness fixes from the bug-hunt (the clean ones; digest gating + UI counter-scope deferred as they need behavior/design decisions).
- **fix** reject SSRF channel targets at the POST /channels API (save-time guard) (`bb35690`)
  - The authed POST /api/v1/channels endpoint persisted any target with no validation (the bug-hunt's flagged save-time gap). It now runs validate_target_url(target, resolve=False) for webhook/slack/discord kinds and returns 400 (not 500) on...
- **fix** SSRF guard on webhook/slack delivery targets (block + allowlist) (`40fa072`)
  - Operator-supplied webhook/slack channel.target is a URL the server POSTs to with no host validation — an authed user could point it at cloud metadata (169.254.169.254), localhost, or RFC1918 and read the internal response via the capture...
- **fix** alert_delivery security — redact secret URLs, scoped auto-disable, escape email HTML (`106aa50`)
  - Three confirmed security fixes from the alert_delivery bug-hunt (clear low-risk ones; SSRF deferred pending an allowlist design).
- **fix** investor-report Generate button (pre-existing TypeError) (`26deb55`)
  - ui/tab_report.py's 'Generate Investor Report' button passed scope/tone/sections kwargs that build_investor_report has never accepted, so every click raised TypeError against the real engine and surfaced as 'Generation Failed' — the manua...
- **fix** register 'company' rule-template category (pre-existing test failure) (`ac06ecc`)
  - Commit c07055c added a 'company-port-concentration' rule template (category='company', metric port_footprint_hhi) for the COMPANY_CONCENTRATION feature but never added 'company' to ALLOWED_CATEGORIES. Two test_rule_templates tests assert...
- **fix** ops_cli flake — CLI-arg IDs could start with '-' (argparse exit 2) (`b764cb5`)
  - Root cause: secrets.token_urlsafe draws from [A-Za-z0-9_-], so ~1 in 64 ids start with '-'. user_id and token_id are passed to the operator CLI as arguments (ops_cli ... --user-id <id>, ops_cli tokens revoke <token_id>); argparse reads a...

### 🛠 Tools

- **tools** briefing_tldr_cli — print the day's TLDR to stdout (`f2c2378`)
  - The design's noted follow-on, now unblocked: the scheduler primes the day-cached narration, so a CLI can read it headlessly and distill the TLDR (a cache hit — no extra Claude call) for ad-hoc piping to SMS/Slack/email. Follows the forec...

### ✅ Tests

- **test** make test_rate_limit_allows_after_refill_interval latency-independent (`1ad5d09`)
  - The drain phase used a 5 tokens/sec refill against real HTTP requests; under a loaded test runner the per-request latency (~170-350ms of server-side work on /alerts) refilled ~1 token per call, so the bucket never emptied and the test fa...

### 📦 Other

- **security** TOTP replay protection (#5) — login codes are single-use (`edd0d25`)
  - A TOTP code was valid for its whole ±window (~90s at window=1), so a captured login code could be replayed until it aged out. Track the highest TOTP step an account has authenticated with and reject any reuse:
- **security** accept recovery codes at login (#6) — close the lost-authenticator lockout (`bffbe58`)
  - auth.mfa had a complete, tested recovery-code system (generate / verify-and- consume, single-use, constant-time) but auth.users.login only ever tried verify_totp — so recovery codes were mintable yet UNREACHABLE from the actual login flo...
- **security** API-token expiry/TTL (#12) — leaked PATs no longer valid forever (`ba51c1e`)
  - verify_token checked no expiry, so a leaked Personal Access Token stayed valid until explicitly revoked. Add an optional expiry across the stack:
- **security** claim signup invite atomically BEFORE creating the user (#10) (`d2f5ee4`)
  - auth.users.signup consumed the invite AFTER inserting the user row, with a consume failure merely logged. Two concurrent signups on the same single- use token (different usernames) BOTH pass the read-only validation, BOTH INSERT, and onl...
- **security** brute-force throttles — login (per-username) + API-token (per-IP, pre-verify) (`59dc2ce`)
  - Resolves the brute-force theme from the auth/ bug-hunt (findings #1, #2; #3 already handled). "Lenient" posture — generous bursts, fail-open.
- **harden** auth — enumeration-resistant login, constant-time calendar token, bounded HMAC buckets (`5643a46`)
  - The 3 low-risk, no-policy-decision findings from the auth/ bug-hunt.
- **harden** report-lede telemetry source + a11y (lede-review follow-ups) (`f079f52`)
  - Adversarial review of the investor-report lede surfaced three real items:
- **wire-up** TLDR lede in the delivered investor report (HTML + markdown) (`b2670d0`)
  - Extends the TLDR to the daily investor report the scheduler actually delivers. AIAnalysis gains a 'tldr' field, set post-build by _build_report_tldr: an adapter maps the report's AIAnalysis (executive_summary -> body, top_recommendations...
- **wire-up** dispatch daily-briefing TLDR to opt-in channels (`0bec260`)
  - Completes the delivery loop. delivery.briefing_tldr.send_briefing_tldr dispatches the day's TLDR to one DeliveryChannel, mirroring engine.operator_digest.send_operator_digest — it reuses the engine.alert_delivery transports (so timeout /...
- **wire-up** daily-briefing TLDR delivery via the scheduler (`1acd5a4`)
  - Realizes the TLDR module's stated delivery purpose (SMS/email/Slack) on the surface a design pass found cleanest: the once-per-day scheduler tick. It has a native DailyNarration type-fit (no adapter, unlike the investor-report path), is...
- **harden** TLDR + opaque_id robustness (adversarial-review follow-ups) (`f97b7a0`)
  - Four latent issues found by an adversarial review of the prior two commits (none live in production today; all now fixed + tested):
- **wire-up** daily-briefing TLDR lede + telemetry fix (`448e8b7`)
  - New engine/daily_briefing_tldr.py distills a DailyNarration into a 2-3 sentence plain-prose lede (Claude Haiku, template fallback). Wired into the Daily Briefing tab above the headline via a new ui.styles.tldr_lede helper (role=note, wit...

## 2026-05-26

### ✨ Features

- **feat+port-supply** daily snapshot persistence + worker job (`95989c7`)
  - Closes the 'snapshots happen automatically' gap. The diff CLI shipped in 5f0dc4e lets an operator compare two on-disk snapshots; this layer captures + stores them on a daily cron so the operator wakes up to 'here's what changed overnight...
- **feat+port-supply** Excel .xlsx workbook export (6 sheets in one file) (`dc462fd`)
  - Where the 5 CSVs are great for scripting, analysts often prefer a single workbook they can pivot across — this ships exactly that.

### 🎨 UI

- **ui+port-supply** per-port + regional deficit trend charts (`d2fc7d3`)
  - processing/port_supply_trend.py walks the snapshot history and returns per-port + per-region trend series. ui/plots/port_supply_trends.py builds the plotly figures with severity-band shading. tab wires both into a new historical-trends s...

### 🔌 API

- **api** GET /api/v1/ports/supply-lines.xlsx — Excel workbook over HTTP (`26c5390`)
  - Completes the three-reach-path surface for the port supply lines exports:

### 🔧 Ops

- **worker+telemetry** query wrapper + contract tests for the run-log (`d80c54f`)
  - processing/worker_run_query.py exposes a clean read API (query_runs, aggregate_job_stats) for consumers of the @_track_run output. Spec tests in test_worker_run_log_contract document the contract so silent breaks are caught.
- **scheduler** wire run_port_supply_snapshot_job into main() (`7905cf2`)
  - Daily worker tick now persists today's port-supply snapshot under cache/port_supply_snapshots/<date>/ and logs the diff vs the prior snapshot inline (severity_shifts / entered_deficit / exited_deficit / deficit_moves) — operators see ove...

### 🛠 Tools

- **tools+docs** cli_index — auto-generated CLI registry + markdown index (`7e8d5ae`)
  - Walks tools/ + cli/ + processing/ for callable __main__ entries; introspects argparse via _build_parser conventions, module attrs, and AST fallback for parsers built inside main(). Emits: - docs/CLI_INDEX.md (human reference) - docs/cli_...
- **tools+port-supply** bulk-diff CLI — aggregate snapshot deltas over a window (`6783080`)
  - Walks N consecutive days of snapshots + ranks ports by: - n_days_in_deficit - cumulative_deficit_day_delta - n_severity_shifts - n_entered_deficit / n_exited_deficit - worst_single_day_delta
- **tools+port-supply** snapshot diff — track trends between two summary CSVs (`5f0dc4e`)
  - Closes the original 'track trends between the ports' ask from the user's initial spec. Without a full historical-persistence layer in the database, a diff CLI that compares two on-disk snapshots delivers the operationally-important patte...
- **tools+port-supply** CLI bulk-exporter for the five CSV views (`3b9f7a4`)
  - \`python -m tools.port_supply_export\` dumps any/all of the five utils.port_supply_csv views to a directory in one command. Useful for:

### ✅ Tests

- **tests** end-to-end test for worker.scheduler.main() — full daily tick (`a39c71b`)
  - Calls main([]) once with all external resources stubbed and asserts: - completes without raising - invokes every known run_*_job (filtered to jobs actually present in the live scheduler — robust to in-flight additions) - continues after...
- **test+api** bump backtests/health validator count from 9 → 11 (`9cb531d`)
  - The 11th validator (Company Supply Risk Stability) and the prior 10th (Port Supply Lines) landed since this assertion was last touched.

### 📦 Other

- **wire-up** cargo mix history + CARGO_FLOW_ANOMALY alerts, company risk history, spillover UI (`2fceb15`)
  - Closes the loop on three of the five analytics modules: each now has either a persisted history stream feeding live alerts or a UI surface operators can read.
- **wire-up** COMPANY_CONCENTRATION alerts + SSI attribution narration + 3 backtest validators + /spillover-graph API (`c07055c`)
  - engine/alert_engine_v2.py: * check_company_concentration_alerts — fires HIGH at HHI>=0.45, CRITICAL at >=0.85 over the live company port footprints. Wired into generate_alerts so the daily tick picks it up. * Adapter in processing.compan...
- **analytics** 5 new pure-function modules — SSI attribution, concentration, cargo-flow, capacity-vs-demand, port spillover (`9998d31`)
  - processing/ssi_attribution.py Live decomposition of any ShippingStressReport into per-component + per-route weighted contributions. Reconciles to the fleet-wide score so a UI can render "component X drives N% of today's stress". top_comp...
- **follow-up** snapshot integrity scheduler hook, 2 new API endpoints, 14th validator, forecast CLI (`20cebcd`)
  - worker/scheduler.py: * run_snapshot_integrity_check_job — wraps check_all_snapshots over the last 14 days. Called from main() between multi-container fan- out and GC. Logs unhealthy counts inline so operators see drift the same tick it a...
- **follow-up** 13th validator, snapshot integrity CLI, anomaly-aware shock digest (`9762d4a`)
  - tools/backtests.py: * _run_historical_event_replay registered as the 13th validator (replays 8 registered historical events through the alert engine, headline = pass rate, healthy if >= 70%). * docs/backtest-baseline.json regenerated; co...
- **follow-up** wire GC + multi-container into scheduler, register 12th validator, backfill tests (`d030d4d`)
  - worker/scheduler.py: * run_port_supply_snapshot_gc_job — wraps gc_old_snapshots with the canonical contract (never raises, returns count dict, @_track_run). * run_multi_container_snapshot_job — wraps the per-container fan-out. * Both cal...
- **processing** forecast accuracy tracker + snapshot integrity checker (`08b8ed8`)
  - forecast_accuracy_tracker — pure-function pairing of logged forecasts to actuals by horizon, with MAE / sign-agreement aggregation. Persistence to jsonl.
- **port-supply+multi-container** coordinator for per-container daily snapshots (`2eefd8c`)
  - New per-container coordinator fans the daily snapshot job out to every modeled container type. Failures isolated per-container (one bad container doesn't kill the others). CLI included for ad-hoc operator runs.
- **replay** historical-event replay validator (alert-engine ↔ event-registry loop) (`4a7cb56`)
  - For each registered historical event, replays the conditions into the alert engine + checks the right alert kinds fire at the right severity. Surfaces missing/unexpected/wrong-severity mismatches as the headline metric.
- **ssi-correlation** lag-correlation analysis — does SSI lead port deficits? (`6965ae8`)
  - Pure-function analyzer measures Pearson/Spearman r between an SSI history and a deficit history shifted by lag=0..N days. analyze_leading_indicator_relationship picks the best-fit lag.
- **port-supply** regional snapshot save/load + retention policy + GC helper (`a361677`)
  - processing.port_supply_history now persists port_supply_regional_rollup_<container>.csv alongside the per-port summary in the same date dir + offers gc_old_snapshots(policy=RetentionPolicy(keep_days=90, keep_first_of_month=True, keep_fir...
- **delivery+port-supply** HTML/text shock digest from snapshot diffs (`c387eea`)
  - Daily snapshot job now emits a ready-to-send overnight digest (HTML + plain-text + subject line) into the snapshot date dir when the diff carries material content. Downstream channels pick up the artifacts. Quiet days skip persistence en...
- **company-supply-risk** per-ticker risk score + 11th backtest validator (`2c7d022`)
  - CompanySupplyRiskScore aggregates per-company exposure to port-side supply problems into a 0-100 scalar (weighted_deficit_days + critical_port_count + port_concentration_penalty). Risk band (Low / Moderate / Elevated / High / Critical) p...
- **anomaly** snapshot-diff magnitude detector — flag shock days vs trailing median (`c297ecd`)
  - Reduces a diff to a single composite, then MAD-z-scores against a trailing 30-day window. Bands: normal / elevated / shock. Pure function — wiring into delivery channels lands next.
- **supply-shock** scenario engine + CLI — quantify what-if port shocks (`9a9a6be`)
  - Pre-canned scenarios (Suez closure, Shanghai 50%, Panama drought) re-derive port-supply chains under the shock + rank affected companies by total deficit-day delta vs baseline.
- **csv+port-supply** derived analytics + Excel-friendly + 2 new views (`6eadaa6`)
  - Five exporters now, each producing a CSV that's genuinely useful in a spreadsheet without a downstream JOIN. Schema bumped to v2.

## 2026-05-25

### ✨ Features

- **feat+port-supply** CSV exporters + 3 download buttons in the tab (`54400c8`)
  - Three CSV views since the supply-lines data answers three operator questions:
- **feat+port-supply** voyage-arc overlay on the map + company→port reverse drilldown (`887b6ee`)
  - Extends the Port Supply Lines tab with two natural follow-ups:
- **feat** Port Supply Lines — map + per-port exposure chain to companies (`0cf2232`)
  - New analytical surface answering "which ports have surplus / deficit container supply, and which publicly-traded shipping companies are exposed to the supply lines flowing through them?"

### 🎨 UI

- **ui** a11y + UX — badge aria-label + tab_booking 'unavailable' semantics (`a80dd9a`)
  - Two small but broad-impact fixes from the UX-quick-wins audit:
- **ui+data-health** unified Backtest Coverage panel — one row, four validators (`8cc350d`)
  - Ties together the four backtest modules shipped today into a single operator-facing summary row at the top of the validation section. Each card surfaces the headline KPI from one validator + a sublabel naming the tab/section where the fu...
- **ui+data-health** LLM cost-by-source bars (replaces by-source table) (`44cf439`)
  - The LLM Usage panel showed by_source and by_model as side-by-side tables. The by_source breakdown is more useful as a chart — operators want to spot which caller dominates spend at a glance, not parse a 4-row table.
- **ui+idea-engine** top-ideas conviction bars + lock-in tests (`d4282c0`)
  - The Idea Engine had a Hero card (top idea) and a Ranked Table but nothing in between to let the operator scan all surfaced ideas' conviction in one glance. Adds a horizontal bar chart between Hero and Ranked Table that plots the top 12 i...
- **ui+data-health** per-tab latency median × p95 scatter + lock-in tests (`1cb7ee2`)
  - The platform's operator dashboard (~3,400 LOC across 22 panels) carried only two charts pre-existing. The Tab Performance panel showed a slow-tabs table with median + p95 columns but the operator couldn't see WHICH tabs had heavy-tail la...
- **ui+alerts** alert-effectiveness volume × hit-rate bubble + lock-in tests (`939b89c`)
  - The Alert Center is the platform's largest tab (~4,900 LOC, 21 panels) but carries only one chart across all of it. The Alert Effectiveness panel breaks alerts down by-type and by-severity as tables, but the operator can't see the two qu...
- **ui+report** sentiment-trend chart in Report History + lock-in tests (`7b105a9`)
  - The Report History section listed past reports as a wsj_market_table with per-row sentiment chips but had no temporal view — the reader couldn't spot regime shifts (bullish-to-bearish runs) without scanning each row. Adds a line chart ab...
- **ui+voyage-tracker** fleet delay-distribution histogram + lock-in tests (`4165eaf`)
  - Closes the disruption-alpha pipeline trio in this session — stage 1 (Voyage Tracker), stage 3 (Macro Projection), stage 5 (Equity Signals) now all carry purpose-built visuals + lock-in tests.
- **ui+overview** signal-conviction heatmap + lock-in tests (`72728fe`)
  - The Overview tab's "Signal Conviction" section rendered a 4×4 corridor × commodity grid as a wsj_market_table. The table reads well for precision but the eye can't spot patterns across the grid at a glance. Adds a plotly heatmap above th...
- **ui+assistant** session topic distribution + question classifier + lock-in tests (`9b2c889`)
  - The Q&A Assistant tab carried zero plotly charts — purely text/tables/ buttons. Adds a small "Session Focus" visual to the right sidebar that shows how the user's questions break down by topic (Freight Rates, BDI, Red Sea, Panama, Equity...
- **ui+briefing** forecast quadrant scatter + lock-in tests (`cd3709b`)
  - The Daily Briefing tab carried zero plotly charts — purely text/tables. Adds the first visual: a today × 30-day-forecast stress scatter for every tracked route, colour-grouped by trend (Worsening / Stable / Improving), marker size propor...
- **ui+portfolio** per-position risk × return scatter + lock-in tests (`f3dac2b`)
  - Adds the canonical "where is risk concentrated?" cross-section that the Portfolio tab was missing — each holding plotted at (Beta, total P&L %) with marker size scaling to portfolio weight and colour following P&L direction. Reference li...
- **ui+macro-projection** SSI component-decomposition bar chart + lock-in tests (`9316984`)
  - Surfaces the SSI's per-component breakdown visually for the first time in the Macro Projection tab. The existing driver→dimension table tells you which SSI components are pushing which SCHI dimensions; the new chart shows *which componen...
- **ui+equity-signals** conviction × 30-day-move scatter + lock-in tests (`96aadea`)
  - Adds the alpha question to the cascade consensus strip — a plotly scatter of every priced idea at (conviction, 30-day move), colour-coded by direction (Bullish / Neutral / Bearish), marker size proportional to cascade depth, with referen...
- **ui+equipment** top-of-tab health bullet + alert-severity lollipop (`3aa1c06`)
  - Two purpose-built visuals close the remaining gaps in tab_equipment:
- **ui** tab_equipment design-system migration + lock-in tests (`6e62a6a`)
  - Route the last hand-rolled inline markup in tab_equipment through ui.styles (status_badge for the regional legend + risk chips, section_divider for sub-section headers, live_data_badge for tab-level provenance). Pin the contract with a 6...
- **ui** wire disruption explainer into Disruption Radar + Voyage Tracker tabs (`5a51a47`)
  - engine/disruption_explainer.py (commit 9aeca39) ships pure-function templates that explain WHY a route is stressed or a voyage delayed. This commit surfaces those explanations to operators inside the two relevant tabs.
- **ui+tools** full-text alert search panel + rules CSV import/export (`a2f1c70`)
  - Two operator surfaces landing together (both touch ui/tab_alerts + docs/DEPLOYMENT.md). Different file zones everywhere else.
- **ui+docs** retry queue panel in Data Health + DEPLOYMENT section (`e5137ef`)
  - Finishes commit ba6777c's deferred UI + docs work. Engine + schema + worker + CLI + API shipped in that commit; this one wires the panel into Data Health and documents the surface area.

### ⚙️ Engine

- **engine+ui** channel auto-disable — circuit breaker after 10 consecutive failures (`e97e7bc`)
  - Stale webhook URLs today fail forever, wasting deliveries + adding log noise. New circuit breaker tracks consecutive failures per channel; at threshold (default 10) auto-flips enabled=False AND fires a CHANNEL_AUTO_DISABLED alert through...
- **engine+api** schema v26 delivery retry queue — persist failed dispatches with exponential backoff (`ba6777c`)
  - When a webhook/slack/email dispatch fails with a retriable error (HTTP 5xx, connection timeout, SMTP temporary failure), the alert was previously logged and lost. Now it persists in a retry queue; worker walks every 5min with exponential...

### 🔌 API

- **api** GET /api/v1/ports/supply-lines — port supply state + exposures as JSON (`57f82ed`)
  - Exposes the same per-port chain data that the Port Supply Lines tab consumes, so external tools (portfolio monitors, alert dashboards, custom scripts) can pull port supply state + exposed-company chains without scraping the UI.
- **api+docs** document GET /api/v1/backtests/health in the OpenAPI spec (`8fc9d05`)
  - The endpoint shipped in 8d70e4c but the OpenAPI spec didn't know about it — external SDK generators (openapi-generator-cli, Redoc, etc.) would have produced a client missing the new probe.
- **api** GET /api/v1/backtests/health — public analytical-layer probe (`8d70e4c`)
  - Surfaces the consolidated backtest report as a public HTTP endpoint so external monitoring (Datadog HTTP check, k8s liveness probe, Pingdom, status page) can alarm on the platform's analytical-layer health without managing per-user tokens.
- **ingress+ui** ICS calendar feed for incidents — subscribe in Google Calendar / Outlook / etc. (`7c049c8`)
  - Operators today have to dashboard-check incidents. Now they subscribe to a per-user iCalendar URL once + their calendar app shows shipping incidents alongside their meetings. RFC 5545 compliant; stdlib only.

### 🛠 Tools

- **tools** consolidated backtest CLI — one command runs all 8 validators (`2cc7b60`)
  - tools/backtests.py ties together the 8 per-module validators shipped today into a single operator + CI-facing CLI. Each adapter normalises its validator's heterogeneous output into a uniform BacktestResult (name + headline label/value +...
- **tools** test flakiness tracker — JUnit XML ingest + flake/slow analytics (`f74d120`)
  - Suite is at 5300+ tests + still growing. Flaky tests hide regressions (test_mfa_enable_persists_flag, test_rate_limit_allows_after_refill_ interval have both surfaced repeatedly this session). Tool ingests pytest JUnit XML, persists resu...
- **tools** bash + zsh tab-completion auto-generator for all CLI tools (`4c06b9f`)
  - CLI surface has accumulated 30+ subcommands across ops_cli + 7 other tool CLIs. Auto-generated tab-completion makes them discoverable. Pure-stdlib introspection of the live argparse tree — no hand- maintained scripts to drift.

### 📚 Docs

- **docs** BACKTESTS.md — operator-facing reference for the 9-validator layer (`c31e45d`)
  - Checked-in markdown reference page covering the platform's analytical backtest layer. Three sections:
- **docs** DEPLOYMENT.md — operator digest + flap detector + escalation end-of-chain (`80fccfe`)
  - Three additions:
- **docs** DISRUPTION_ALPHA.md — 6th SSI component + backtest layer + lock-in suite (`c240995`)
  - Three corrections + two new sections:
- **docs** MODELS.md — SSI 6th component + 3 new backtest module sections (`6949eff`)
  - Two corrections + an expansion:
- **docs** AUTH.md — reflect shipped multi-user + MFA + invitations + tokens (`f7ab8fa`)
  - AUTH.md was the most stale doc in the tree — opened with "This is not a multi-user system" and stated "no per-user accounts, no per-user data, no users table in the DB" while the platform actually shipped multi-user auth months ago (sche...
- **docs** regen audit-baseline.csv after today's 13-tab refactor + 2 backtest push (`cedba67`)
  - LOC grew 100-200 per touched tab from the new visuals + helpers; inline_divs counts unchanged everywhere — the per-tab lock-in budget caps held.
- **docs** regen auto-generated artifacts (CHANGELOG / SCHEMA / openapi / completion) (`55b29b7`)
  - CHANGELOG.md — 323 commits via tools.changelog_cli --since 90d docs/SCHEMA.md — live DB schema at v26 docs/SCHEMA_HISTORY.md — 25 migrations from state/migrations.py docs/openapi.json — 169KB spec via tools.openapi_cli json docs/openapi....

### ✅ Tests

- **test** harden rate-limit refill timing test against scheduler preemption (`4e90ec6`)
  - test_rate_limit_allows_after_refill_interval was flaking on full- suite runs (passed in isolation, occasionally failed under load). Root cause: 0.3s sleep at 5 tokens/sec yielded 1.5 tokens — under suite load the test runner can lose eno...

### 📦 Other

- **narration** wire port-deficit context into the daily briefing (`1102136`)
  - The morning LLM-narrated briefing now incorporates today's worst port container deficits + their exposed tickers, surfacing a port-supply paragraph alongside the existing SSI / cascade-idea / route-forecast sections. Operators reading th...
- **rule-templates** add 'Port container deficit >3 days' template (`2f27f65`)
  - Wires PORT_DEFICIT alerts into the UI rule-editor template catalogue. Without this template, the new alert type exists in the engine + fires from generate_alerts() but isn't discoverable from the rule picker — operators would have to han...
- **backtest** Port Supply Lines stability — completes the 10-validator set (`cf2f14f`)
  - Closes the only gap remaining in the analytical-layer backtest coverage: every other surface has a validator + Coverage card + CLI adapter, but the port supply-lines model (today's new feature) didn't yet. Adds it.
- **alerts** PORT_DEFICIT — wire port supply-state into the live alert engine (`bcca2fd`)
  - When a port crosses into deficit, the existing alert delivery substrate (Slack/email/SMS/Discord/webhook/PagerDuty + escalation chains + quiet hours + dedup + cooldown) now picks it up automatically.
- tools.backtests: --verbose / -v renders per-class scorecard rows inline (`14d710b`)
  - When a validator goes red, an operator currently has to drop into the Python REPL + import the validator module + dump its scorecards to see the per-class breakdown. --verbose folds that triage path into the CLI:
- **ci+docs** commit backtest baseline + add drift gate (`88d9cf6`)
  - Adds docs/backtest-baseline.json — the reference snapshot of all 9 validators in their current healthy state — and wires `python -m tools.backtests --compare-baseline` into CI as a second gate after --strict.
- tools.backtests: baseline save/compare for drift detection (`d7b0804`)
  - --strict is a one-sided gate: a refactor can move chokepoint sign-agreement from 80.5% → 65% (still above the 0.55 healthy threshold) and --strict accepts it. The baseline workflow catches that drift.
- **eta-predictor** per-label + scalar accuracy backtest — completes the 9-validator set (`db73271`)
  - Last major analytical module without a backtest. processing.eta_predictor emits two outputs nobody was scoring: a scalar predicted_delay_days and a categorical congestion_risk label (LOW / MODERATE / HIGH / SEVERE).
- **ci** gate the analytical layer on tools.backtests --strict (`0212524`)
  - Adds a CI step right after pytest that runs the consolidated backtest CLI in strict mode. All 8 validators must report healthy on the bundled synthetic-history generators; --strict exits 1 on any calibration / monotonicity flag flip.
- **backtests** news-sentiment + vulnerability-scorer predictiveness + coverage (`ec5a3c2`)
  - Seventh and eighth backtest modules. Brings the validator layer from six to eight; the unified Backtest Coverage panel in tab_data_health now flows in two rows of four.
- **leading-indicators** per-signal-class predictiveness backtest + coverage card (`67954f7`)
  - Sixth backtest module. processing.leading_indicators carries 12+ FRED series each tagged with a BULLISH/BEARISH/NEUTRAL signal + a stated lead_time_weeks for shipping demand. Nothing was asking whether each signal actually leads to the d...
- **freight-volatility** regime + mean-reversion predictiveness backtest (`b91aa0d`)
  - Fifth backtest module of the day. processing.freight_volatility classifies each route per snapshot into a regime (TRENDING_UP / TRENDING_DOWN / BREAKOUT / RANGING) and a mean-reversion signal (OVERSOLD / NEUTRAL / OVERBOUGHT) — nothing w...
- **schi** per-dimension predictiveness backtest — symmetric to the SSI validator (`05f0b44`)
  - The SSI got a 3-axis validator triad (predictiveness + horizon-decay + collinearity) in 7394f1d/00bc008/750308e. SCHI — the other major composite sitting next to SSI in tab_macro_projection — had none. This mirrors the SSI work, dimensio...
- **momentum-ranker** per-signal-class backtest + UI panel + property tests (`f5a997b`)
  - engine.momentum_ranker classifies assets into a STRONG_SELL → STRONG_BUY ladder via a composite of 7d/30d/90d momentum. Nothing in the platform asked the question this module answers: across a history of past signals, did STRONG_BUY obse...
- **disruption-forecast** accuracy backtest + UI panel + property tests (`756a7a9`)
  - The processing.disruption_forecast module emits 7d and 30d stress projections per route, but nothing in the platform scores those forecasts against what actually happens. Closes the gap.
- **ssi** collinearity analyzer — third leg of the component-validation triad (`750308e`)
  - Per-component sign-agreement (7394f1d) answered "which components are predictive?". Horizon-decay (00bc008) answered "...and at what horizon?". This adds the missing third leg: are any two components secretly double-counting the same sig...
- **ssi** horizon-decay scan + heatmap — extend the component backtest (`00bc008`)
  - Builds on the per-component sign-agreement backtest from 7394f1d. The natural follow-up: at WHAT horizon does each component carry its edge? Adds validate_ssi_horizons(history, horizons=...) which runs the same sign-agreement check acros...
- **ssi** per-component predictiveness backtest + UI panel + property tests (`7394f1d`)
  - Adds processing/ssi_component_validation.py — a deterministic backtest that answers a question the static COMPONENT_WEIGHTS in processing.shipping_stress_index cannot: of the six SSI components (chokepoint, congestion, weather, rate, vul...
- **disruption-alpha** SSI backtester + per-route explainer — validates + narrates the signal (`9aeca39`)
  - Closes the disruption-alpha gap identified in the session plan: the SSI / cascade / equity-signal pipeline was complete-looking but had zero empirical validation and no operator-facing rationale. This commit ships both.
- **ssi** 6th component (anomaly drift detector) + historical-events registry (`61d4d81`)
  - Two pieces of disruption-alpha audit work that landed together:

## 2026-05-24

### 🎨 UI

- **ui+engine** Rule History tab — fire timeline + ack-rate + audit trail per rule (`69abd41`)
  - Operators today can't easily answer 'is rule X firing too often?' or 'how often does the trading desk ack alerts from rule Y?' — they have to grep the alerts table by hand. New tab pivots the alert view around a selected rule.
- **ui+tools** report-to-report diff — structured comparison + Markdown/HTML/JSON rendering (`23b091f`)
  - Operators today have no way to compare reports beyond opening two browser tabs. New utils/report_diff produces a structured diff (signals added/removed, route value changes, sentiment/risk shifts) with three render targets.
- **ui+worker** Worker Health dashboard + decorator-tracked job runs (`b1e0caa`)
  - Operators today can't see worker state without grepping logs. New state/worker_runs persistence + ui/tab_worker_health dashboard show every background job's status, last run, duration, and result.

### ⚙️ Engine

- **engine** time-series anomaly detection — flag drift below static thresholds (`94c326a`)
  - Rules today fire only on absolute thresholds. Anomaly detector catches subtler patterns: BDI drifting 2%/day for 10 days, freight cost slowly diverging from its 30d baseline. Three statistical methods + per-metric cooldown.
- **engine+ops** weekly digest — Monday summary auto-dispatched to channels (`0247742`)
  - Every Monday 14:00 UTC (configurable), compose a summary of the week's alerts / incidents / source health / channel budget usage and dispatch through the user's enabled channels. Opt-in per user.
- **engine+ui** audit log search panel — filter + grep + CSV/JSONL export (`04c334d`)
  - Today auth.audit.query_audit only takes user_id + action; the UI panel in tab_data_health is a flat 'recent N events' table. Operators want to: search by user, action prefix, entity_type/id, date range, and free-text grep on detail_json.
- **engine+api** schema v25 per-channel monthly delivery budgets (`aa64e15`)
  - Operators want to cap noisy channels: 'Slack #trading-desk gets max 200 alerts/month; PagerDuty gets max 50'. When budget exceeded, deliveries suppress until the next month starts. Calendar-month rolling — no sliding window.
- **engine** schema v24 alert escalation chains — fire to fallback channel after N min unacked (`1dcb3b6`)
  - When an alert stays unacknowledged for N minutes, escalate to a fallback channel — and another N minutes later → another channel. Per-rule chains, opt-in.

### 🔌 API

- **api+tools** OpenAPI 3.0 spec generator + public GET /openapi.json + docs (`010d627`)
  - 25+ endpoints today have prose-only documentation in DEPLOYMENT.md. Now we have a machine-readable OpenAPI 3.0 spec — clients can auto-generate SDKs, and /api/v1/openapi.json serves the spec live (public; no auth) for tooling like Swagge...
- **ingress** POST /events on webhook listener — accept external alerts (HMAC or bearer) (`e7afe20`)
  - External monitoring tools (Datadog, Sentry, Grafana, custom scripts) can now POST alerts directly into the Ship Tracker pipeline. Two auth modes — HMAC for headless integrations, bearer for scripts with API tokens. At least one must vali...

### 🔧 Ops

- **ops** escalation chains CLI + API + UI wiring (`d0540c6`)
  - engine/alert_escalation.py (commit 1dcb3b6) is the engine; this commit gives operators the surface to configure chains without writing SQL.
- **auth+engine+api** per-user notification preferences (severity/types/quiet hours) (`df4ad34`)
  - Per-user filter on TOP of the per-rule + per-channel routing. Each operator now subscribes to their own severity floor, alert-type allow- list, severity→channel allow-list, and quiet-hours window. Layered AFTER existing rule + channel fi...

### 🛠 Tools

- **tools** alert replay + DB anonymizer — two operator tools (`6d1109e`)
  - Both agents finished in parallel and share docs/DEPLOYMENT.md, so landing together. Different files everywhere else.
- **tools** auto-generated CHANGELOG.md from git log — Conventional Commits parser (`26f6ee2`)
  - Hand-curated changelog wasn't scaling — at 300+ commits the manual maintenance burden was real. Tool parses git log + Conventional- Commits prefixes (feat:/fix:/ui:/engine:/api:/ops:/tools:/docs:/test:) into a date-grouped Markdown. Re-r...
- **tools** schema_docs — auto-gen SQLite schema + migration history as Markdown (`0695621`)
  - Until now schema docs were scattered across commit messages and migration code. New tool introspects the live DB + parses migrations.py to produce a stable Markdown reference.

## 2026-05-23

### 🎨 UI

- **ui(nav)** tab favorites — pinned cluster at top of sidebar (`9c49f62`)
  - App has 60+ tabs; each operator uses a different subset. Pinning surfaces the 3-5 tabs each user actually visits at the TOP of the sidebar, above the section nav. Zero schema bump — persisted in UserSettings.extras (generic dict, no new...
- **ui+api** Markdown export for reports — utils + UI download button + API endpoint (`3401a68`)
  - Today reports are PDF only — Markdown is shareable on GitHub / Notion / Slack threads / etc. without PDF tooling. Self-contained MD generation, no external markdown library required.
- **ui(overview)** per-panel CSV download + zip-bundle button (`4510c0b`)
  - Operators want to grab data out of the overview for offline analysis without scraping the page. Each panel gets its own download button + a top-of-tab 'Download all panels (zip)' button.

### ⚙️ Engine

- **engine+api** schema v23 alert annotations + per-tab perf budgets (`b4a2bb7`)
  - Two features that share tools/ops_cli.py + tests/test_ops_cli.py + docs/DEPLOYMENT.md so they land together.
- **engine+tools** source-health auto-alerts + rules YAML config-as-code (`222e129`)
  - Two operator-tooling features that share tools/ops_cli.py + tests/test_ops_cli.py + docs/DEPLOYMENT.md (and the prior commit's rate-limit docs section that landed in the same diff).
- **engine** anti-flap detector — consolidate oscillating rules into single FLAP alert (`bcf0ba0`)
  - Today a metric oscillating around a threshold can fire+resolve+fire+ resolve repeatedly, spamming channels. Flap detection counts threshold crossings in a sliding window; when crossings >= threshold, emits ONE consolidated FLAP alert ins...
- **engine+ui** schema v19 bulk alert acknowledgment with optional note (`c67fc71`)
  - Today operators click each alert individually to ack — 30 LOW alerts = 30 clicks. Bulk acks a filtered set in one operation; persists an optional context note + records who ack'd.
- **engine+ui** 15 alert rule templates + cooldown input in rule editor (`82f52c7`)
  - Lowers cold-start for a new operator: 15 pre-baked AlertRule templates spanning macro / route / port / event / cost categories, exposed via 'Rule templates — quick-add from catalog' expander in the rule editor. Also wires the cooldown_mi...
- **engine** schema v18 per-rule cooldown — suppress same rule firing > N min (`a1c090b`)
  - Today an AlertRule fires every evaluation when its condition stays tripped — spamming downstream channels. Per-rule cooldown_minutes suppresses re-firing inside the window.

### 🔌 API

- **api+engine** audit JSONL export + schema v22 alert silences (`f1ae44e`)
  - Two operator-tooling features that share tools/ops_cli.py + worker/api_server.py + tests/test_*.py + docs/DEPLOYMENT.md (and the db_check_cli docs section that landed in the same diff).
- **api** per-user token-bucket rate limiting on all /api/v1/* (except /health) (`6cc40c4`)
  - A misbehaving client today can hammer endpoints unchecked. Per-token rate limiting (in-process token-bucket) returns 429 + Retry-After when exceeded. Health endpoint stays exempt so load balancers can probe.
- **api** schema v17 password-protected public reports + audit/incidents/source-health read endpoints (`ae59900`)
  - Two API-surface expansions that landed together (same files):

### 🔧 Ops

- **auth+scheduler+api** schema v20+v21 — report scheduling + MFA recovery codes + invitations (`1953836`)
  - Three coordinated features that landed via parallel agents and share schema bumps (v20 + v21) + tools/ops_cli.py + worker/api_server.py. Committed together because the file-level split would be tedious + artificial.
- **ops** per-channel 'Send test ping' button + send_test_ping helper (`4f947da`)
  - Operators today have to manufacture a real alert to verify a channel works. New button + engine helper lets them dispatch a synthetic test message without firing the alert pipeline.

### 🛠 Tools

- **tools** db_check_cli — SQLite integrity verifier with optional auto-fix (`9841510`)
  - Operators want to verify their DB hasn't corrupted (after a crash, before a backup, during incident response) and get a clear report of issues. Read-only by default; --fix opts into safe cleanups.
- **tools** backup_cli — snapshot/restore the state DB + reports tree (`f39100a`)
  - Today operators have no in-app way to snapshot before a risky change or recover after one — they'd have to know the file paths. Pairs with the existing utils.bulk_export (which is wider-scope but has no restore command).

## 2026-05-22

### ✨ Features

- **feat(security)** vault bulk encrypt/decrypt + UI + webhook 2-secret rotation window (`154d8e1`)
  - Three security improvements that landed together (each agent died partway, but the engine code shipped and tests still pass — the key bug was a webhook _verify_hmac signature change that broke existing single-secret tests; fixed by makin...
- **feat(users)** user_filters — per-user saved filter presets in kv_state (no schema bump) (`e471855`)
  - Cross-app saved filter presets. Users save named combinations like 'all-CRITICAL-last-24h' or 'transpacific-only' for one-click recall. Storage is a single JSON blob per user under kv_state['user_filters: {user_id}'] — no schema change n...
- **feat(alerts)** alert_correlator — group bursts of related alerts into incidents (read-side) (`665487d`)
  - A single market move (BDI spike) can fire BDI_MOVE + RATE_SURGE on several routes + STOCK_MOVE on shipping equities in the same 30-min window. To the operator that's ONE incident, not N. This module groups related alerts at read time wit...
- **feat(i18n)** utils.tz — timezone-aware briefing rendering per user_settings.timezone (`fea4082`)
  - Renders user-facing timestamps in the logged-in user's preferred timezone (from auth.settings.UserSettings.timezone). Falls back to UTC for unauthenticated sessions or any error. Persistence-layer timestamps (SQLite rows, cache files) st...
- **feat(auth)** TOTP MFA — optional 2FA over the password layer (schema v16, stdlib only) (`1ac149b`)
  - Opt-in TOTP second factor on top of the password login. Users who enable MFA need both a correct password AND a 6-digit code from their authenticator app (Google Authenticator, 1Password, etc.). Backward compatible: existing users withou...
- **feat(auth)** per-user settings — timezone / theme / defaults + extras (schema v15) (`9f79568`)
  - Multi-user auth ships identities + per-user data scoping, but there's no place to store PREFERENCES — 'alice prefers America/New_York timezone' or 'bob wants dark mode.' This commit adds the user_settings table + the get/save/update API.
- **feat(ops)** worker retention for audit + reports — audit 365d, reports keep_n=30 (`5227e89`)
  - Round out the worker retention pipeline. audit_events were missing from the daily prune pass (only the engine helper existed). Reports already auto-prune on save but the worker pass catches drift.
- **feat(ops)** vault management panel + prune_old_alerts retention helper (`ccba6ad`)
  - Two adjacent ops improvements:
- **feat(auth+ui)** activate multi-user auth opt-in + Data Source Health panel in tab_data_health (`f2d587b`)
  - Two small but high-impact changes that surface infrastructure already in place.
- **feat(api)** worker.api_server — read-only HTTP API gated by api_tokens (port 8503) (`4ba02fa`)
  - A stdlib http.server-based read API on a new port (8503) for external scripts. Bearer-token auth via auth.tokens.verify_token; per-user data scoping via the token's user_id. Companion to the inbound webhook listener (port 8502).
- **feat(security)** state.vault — stdlib-only field-level encryption for sensitive channel targets (`d356d79`)
  - A small encrypted secrets vault for storing Slack webhook URLs, PagerDuty integration keys, etc. encrypted-at-rest in SQLite. Opt-in per save; existing rows untouched. Master key in env (VAULT_KEY) or auto-generated to kv_state.
- **feat(ops)** tools.ops CLI — argparse subcommands for every common admin task (`3782580`)
  - A single python -m tools.ops entry point covering the operations that today require either the UI or a Python REPL. Letss operators script bulk work and SSH into a running container to fix things without launching Streamlit.
- **feat(alerts)** time-window deduplication — fire_count bump instead of N rows (schema v14) (`8bdad9e`)
  - A flaky data feed that bounces a value across its threshold N times in an hour previously left N rows in the alerts table. Now those get collapsed into one row with fire_count = N. Same alert_type + same severity + same entity (ticker /...
- **feat(ops)** operator_digest — daily email summary of the Operator Dashboard (`e4953c0`)
  - A cron-driven email/slack/webhook delivery of the Operator Dashboard snapshot. For operators who want the daily system summary in their inbox without logging in. Sent to any delivery_channel whose name starts with 'ops-' (convention).
- **feat(ops)** GET /health endpoint on the webhook listener + docker healthcheck (`a83e5f1`)
  - The webhook listener container now serves a public liveness probe at GET /health on port 8502. Returns a JSON status block summarizing schema_version, user count, unacked critical alerts, recent render success rate, and current outages....
- **feat(alerts)** channel quiet-hours — per-channel time-of-day delivery suppression (schema v13) (`15ba620`)
  - DeliveryChannel gains three fields: quiet_start, quiet_end (UTC HH:MM) + quiet_override_critical (CRITICAL always delivers when True). When the current time is in [quiet_start, quiet_end) and the alert isn't CRITICAL-with-override, deliv...
- **feat(ops)** bulk_export — single tar.gz with SQLite DB + cache parquets + manifest for backup/migration (`8ffa07d`)
  - Bundles the durable state into a single timestamped archive that an operator can hand to a colleague, store on S3, or restore from. The tarball captures the SQLite DB, every cache/<source>/ parquet, and cache/reports/*.html. logs/ is int...
- **feat(setup)** first-run setup wizard — admin user creation + env-var guidance + connection test (`1936c17`)
  - A dedicated setup tab that detects a fresh install (no users created yet) and walks the operator through three steps: create the admin user, configure data-source API keys, run a connection test. Once at least one user exists, the wizard...
- **feat(alerts)** per-route alert threshold overrides — kv_state-backed, no schema bump (`6136e36`)
  - Today check_rate_alerts(freight_data, threshold_pct=8.0) applies the same global threshold to every route. Some routes are noisier than others — transpacific eastbound is volatile, transatlantic is stable. Per-route overrides let operato...
- **feat(observability)** data source health pings — periodic liveness checks for every external feed (`84f4583`)
  - Periodically probe each external data feed (FRED, yfinance, WB, etc.) and record up/degraded/down + latency to SQLite. Answers 'is FRED degrading right now?' without grepping logs.
- **feat(auth)** per-user API access tokens (schema v11) + schema v12 groundwork (`d52164c`)
  - Long-lived per-user secrets that authenticate to future API endpoints without needing the user's password. Token is shown exactly once at creation time; only the hash is persisted. Plus the v12 schema table that ships alongside (data_sou...
- **feat(audit)** append-only audit log + record_audit hooks at 11 privileged touchpoints (`6a01323`)
  - Build on the schema v10 audit_events table (created in commit 72178f9 alongside the snapshot work). This commit adds the helper module + the opt-in record_audit hooks at the most security-relevant touchpoints across the alert engine, ale...
- **feat(reports)** InvestorReport snapshot persistence + schema v9/v10 + ReportSnapshot views (`72178f9`)
  - Persist a slim snapshot of every InvestorReport to SQLite so the 'What Changed' diff in tab_briefing (commit d0ed7a4) survives Streamlit restarts. Today the snapshots only lived in session_state and reset on reload.
- **feat(auth)** per-user data scoping — every load function honors user_id with legacy passthrough (`1ebb5ee`)
  - Completes the multi-user auth story (commit 019a7bc added the schema + users table; this commit makes the load functions respect user_id).
- **feat(webhook)** stdlib HTTP listener for inbound alert acknowledgment + PagerDuty (`23bb274`)
  - External systems can now close the loop: when PagerDuty marks an incident resolved, it POSTs a webhook back and the Ship Tracker flips the matching alert's acknowledged flag. Today this was UI-only.
- **feat(telemetry)** perf_telemetry — track each tab's render duration + schema v8 (`b0fa298`)
  - A render-performance telemetry layer that records how long each tab's render(...) function takes. Persists to SQLite so we can answer 'which tabs are slow?' without standing up a real profiler.
- **feat(a11y)** ARIA across custom HTML components + WCAG contrast helper (`cc74d7a`)
  - Streamlit doesn't expose deep accessibility APIs, but the custom HTML we emit (KPI cards, banners, badges, tables, section headers, insight cards) can carry the right ARIA roles + aria-live + aria-label so screen readers actually narrate...
- **feat(auth)** multi-user foundation — users table, signup/login, schema v7 (`019a7bc`)
  - Stand up the multi-user identity surface WITHOUT a big-bang migration. Backward compatible: existing single-password gate keeps working when no users are defined. Per-user data scoping is left for a follow-up PR — this commit just adds t...
- **feat(alerts)** alert_backtest — score alerts against their post-fire realized windows (`5903bce`)
  - Backtest harness for the alert engine itself. Given the historical alerts persisted in SQLite plus the underlying time-series data (stock_data / freight_data / macro_data dicts), evaluate each alert against what happened in the window AF...
- **feat(reports)** report_diff — compute structured diff between two InvestorReport snapshots (`8917053`)
  - A pure-function module that takes two InvestorReport-shaped objects and produces a structured diff: new/dropped alpha signals, top route rate changes, sentiment shift, risk-level change, and a short narrative summary. Also renders the di...
- **feat(alerts)** rule → channel routing — AlertRule.target_channels + deliver_pending_for_rule (`278e334`)
  - Previously the rule layer and the delivery layer were fully decoupled: every enabled channel received every alert that met its severity threshold. There was no way to say 'geopolitics rule fires only to the geo-slack channel'. This commi...

### 🐛 Fixes

- **fix(ui)** silence pandas DataFrameGroupBy.apply FutureWarning in tab_results (`800abde`)
  - The single remaining warning surfaced during the full UI verification. pandas 2.x deprecates groupby.apply operating on the grouping column by default — silenced with include_groups=False, matching the new recommended shape. No behaviour...

### 🎨 UI

- **ui** new Operator Overview tab — at-a-glance status across all telemetry layers (`ecc5c7e`)
  - A single dashboard summarizing alerts, channels, llm spend, tab perf, source health, audit events, and incidents — so an operator sees system status without bouncing between 7 different tabs. Read-only; no engine modifications.
- **ui(security)** in-app MFA setup panel — enroll / verify / disable (`7261689`)
  - auth/mfa.py (commit 1ac149b) shipped TOTP MFA + CLI subcommands (commit 3410139) but had no in-app UI. Users had to drop to a shell to enroll. Now the Security section of Data Health has a self-service panel.
- **ui(alerts)** consume active_filter_payload across incidents + ack + table panels (`7c217c9`)
  - Closes the loop on commit 8d4fb17 — yesterday's Saved Filters panel persisted st.session_state.active_filter_payload but no downstream panel actually filtered on it. Now three panels honor it.
- **ui(alerts)** Saved Filters panel — load / save / delete user filter presets (`8d4fb17`)
  - Wires state.user_filters (commit e471855) into tab_alerts so operators can name & one-click recall filter combinations across visits. Slotted between the page header and the incidents panel — filters affect everything below.
- **ui(alerts)** Active Incidents panel — surface alert_correlator grouping (`6a79e49`)
  - Wires engine.alert_correlator (commit 665487d) into tab_alerts so operators see a single 'incident' row covering 3+ related alerts instead of N separate alert rows. Slotted ABOVE the alert table (incidents are the higher-level view; the...
- **ui(security)** My Security panel — MFA enable/disable + API token management (`5deccd5`)
  - Surfaces auth.mfa (commit 1ac149b) + auth.tokens (commit d52164c) in tab_data_health. Logged-in users can enable/disable TOTP MFA from the UI and manage their API tokens without touching the CLI.
- **ui(alerts)** quiet hours form + per-route thresholds panel (+193 lines) (`0156188`)
  - Two UI surfaces for engine work that landed in earlier commits: 1. Channel quiet hours (commit 15ba620 — schema v13) 2. Per-route alert thresholds (commit 6136e36 — kv_state)
- **ui(audit)** surface audit_events table in a new Audit Log panel in tab_data_health (`d91796c`)
  - Slots into the existing data-health dashboard after the Tab Performance panel. Three filter controls, three KPIs, a styled events table with relative timestamps + username resolution.
- **ui(nav)** operator dashboard sidebar entry + smoke test coverage (`532ba21`)
  - Wires the new ui/tab_operator.py (landed alongside the track_render sweep in the previous commit) into app.py's section-based sidebar nav, and adds it to the parametrized tab smoke harness.
- **ui(briefing)** 'What Changed' widget — diff between current + previous report snapshots (`d0ed7a4`)
  - Surfaces processing.report_diff (commit 8917053) in tab_briefing. Each visit to the briefing tab rotates the prior report into a 'previous' slot in session_state, and the new render shows a structured diff: sentiment shift, risk-level ch...
- **ui(data_health)** Tab Performance panel — render duration + slowest tabs + error counts (`a2ac940`)
  - Surfaces engine.perf_telemetry (commit b0fa298) in tab_data_health. Lets operators see which tabs are slow + where exceptions are happening, without standing up a real profiler.
- **ui(alerts)** Alert Effectiveness panel — backtest hit-rate + magnitude + per-type/severity (`6cc879f`)
  - Surfaces engine.alert_backtest (commit 5903bce) in tab_alerts. Operators can now see at a glance: how often do alerts predict the right direction, by how much do they overshoot or undershoot, and which alert types / severities are signal...

### 🔌 API

- **api** 7 write endpoints (rules / channels / report-public) + 29 tests (`7c82bc0`)
  - worker/api_server.py was read-only at v1 — external scripts now manage rules, delivery channels, and report-share state through the API.

### 🔧 Ops

- **ops(cli)** mfa / filters / incidents / settings subcommands + 24 tests (`3410139`)
  - Recent commits shipped four engine modules without CLI surface — this fills the gap so operators can manage them from the shell without booting Streamlit.

### 📦 Other

- **perf(observability)** wrap every tab's render() with track_render — perf telemetry on all 64 tabs (`7675e9d`)
  - Adopts the engine.perf_telemetry.track_render context manager (commit b0fa298) across every ui/tab_*.py. Previously only tab_overview was instrumented (the reference adoption); now the Tab Performance panel in tab_data_health surfaces re...

## 2026-05-21

### ✨ Features

- **feat(alerts)** digest_mode delivery — format_digest_payload + deliver_digest + deliver_pending dispatch (+33 tests) (`a14bb3c`)
  - Completes the digest-mode work the parallel agent died mid-flight on (socket error). The schema v6 + DeliveryChannel.digest_mode field + save/load CRUD persistence already shipped — this commit adds the actual digest formatting + deliver...
- **feat(reports)** public share link — make_public / revoke_public / load_public_report (schema v5) (`18377d5`)
  - Read-only public-share-link feature on top of the existing report_history SQLite layer. Generate a URL-safe slug + expiry, persist on the report row, expose load_public_report(slug) that returns the HTML only when the link is still valid...
- **feat(alerts)** acknowledgment analytics + schema v4 (alerts.acknowledged_at) (`7d2e4ac`)
  - Adds an analytics layer over the alerts table that answers questions like 'what fraction of CRITICAL alerts get acknowledged within 24h?' or 'which days had the most unacked traffic?'. Schema bump records the acknowledgment timestamp so...
- **feat(alerts)** generic webhook + Discord + PagerDuty delivery kinds + digest_mode persistence (`b8bd33e`)
  - Three new DeliveryChannel.kind branches plus completion of the schema v6 digest_mode CRUD wiring that the parallel digest-mode agent died before finishing.
- **feat(telemetry)** retention policy + CLI pruner — llm_calls table no longer grows unbounded (`e0e801f`)
  - The llm_calls table (commit d888363) records every Anthropic call. Without a retention pass it grows forever. This commit adds a 90-day default cutoff + a CLI entry point + wires it into the daily worker loop so the existing cron entry n...
- **feat(alerts)** SMS delivery via Twilio — DeliveryChannel.kind='sms' now works (`2fb059c`)
  - Third dispatch branch alongside slack + email. Uses Twilio's REST API directly via requests.post (HTTP Basic auth) — deliberately NOT adding the twilio SDK as a dependency since the API call is straightforward and the tighter dep surface...
- **feat(auth)** single-password gate — stdlib-only (no bcrypt/argon2 dep) (`71ef6d9`)
  - Adds a single-password authentication gate at the top of the Streamlit app. INTENTIONALLY NOT multi-user — that's a much larger architectural decision (user_id foreign keys across every persistence table, per-user data scoping, password...
- **feat(worker)** scheduled report worker — daily briefing PDF on cron (`29b26c9`)
  - Builds the daily investor briefing without Streamlit, persists it via the existing report_history layer, optionally pushes it through every enabled delivery channel. Designed for external cron / Docker CMD invocation, not for in-process...
- **feat(alerts)** SMTP email delivery channel — DeliveryChannel.kind='email' now works (`d3254e0`)
  - Extends the alert-delivery layer (commit c9b7664) with an SMTP email backend. DeliveryChannel.kind now dispatches: - 'slack' → existing webhook path - 'email' → SMTP via _deliver_email - other → 'unsupported channel kind' (reserved for f...
- **feat(telemetry)** LLM cost tracking — schema v3 + record_call wired into both LLM engines (`d888363`)
  - Now you can answer 'how much have I spent on LLM calls this week?' from the SQLite store. Every successful Anthropic call is logged with model, tokens in/out, source ('commentary' / 'narration'), and an estimated USD cost computed from a...
- **feat(commentary)** LLM-driven per-tab editorial commentary engine (`57f0559`)
  - A reusable engine that any tab can call to generate 1-2 paragraph WSJ- style editorial commentary on its current data context. Wired into tab_overview as the reference implementation.
- **feat(alerts)** Slack webhook delivery + schema v2 (delivery_channels) (`c9b7664`)
  - The alert engine now persists CRITICAL/HIGH/MEDIUM/LOW alerts to SQLite, but they only surface in the UI. This commit adds an external delivery path so alerts actually reach users when they're not staring at the dashboard.
- **feat(state)** SQLite-backed persistence — alerts, alert rules, report history (`5aa76f0`)
  - Replaces three JSON-file persistence layers with a shared SQLite store at cache/ship_tracker.db. Suite at 3124 passing (was 3113, +11 net).

### 🐛 Fixes

- **fix(ui)** blank-line-then-indent in HTML markdown rendered as a code block (`a0c980d`)
  - What you saw in the browser: raw HTML strings (e.g. '<div style="font- size:0.72rem;color:var(--te...') appearing as plain monospaced text beneath the KPI values, and the entire masthead 'Summary line' block rendering as a fenced code du...
- **fix** two small follow-ups from the coverage findings (`8b6d590`)
  - 1. routes/rate_estimator.py: compute_rate_momentum now guards the missing 'rate_usd_per_feu' column the same way compute_rate_pct_change already does. Previously a populated-but-column-less DataFrame raised KeyError; now returns the neut...
- **fix(excel_export)** _add_footer infinite loop on row-padding (`38084dc`)
  - The footer-padding loop in _add_footer was an infinite loop in the common case. The original shape:
- **fix(fpdf)** migrate cell/multi_cell ln= -> new_x/new_y (fpdf2 2.5.2+ deprecation) (`22019c0`)
  - fpdf2 v2.5.2 deprecated the ln= parameter on cell() and multi_cell() in favor of new_x=XPos.{LMARGIN|RIGHT}, new_y=YPos.{NEXT|TOP}. A single render of utils/investor_report_pdf.py was emitting 8000+ DeprecationWarnings per pytest run.
- **fix(requirements)** add openpyxl — utils/excel_export.py has used it all along (`cae50d9`)
  - Flagged by the test_excel_export.py coverage push. utils/excel_export.py imports openpyxl at module level (Workbook, Font, PatternFill, Alignment, Border, Side) and uses Font/PatternFill/Side as module-level constants OUTSIDE the try/exc...
- **fix(BDIY sweep)** five remaining BDI callers now use BSXRLM with BDIY fallback (`c39b6ea`)
  - Follow-up to the data/fred_feed.py BDIY -> BSXRLM key migration (commit fb5f025). After that change, FRED_SERIES delivers data under the canonical 'BSXRLM' key, but five downstream callers still string-referenced the old 'BDIY' key with...
- **fix(feeds)** tenacity retries now actually fire on transient network errors + BDIY -> BSXRLM canonical key (`fb5f025`)
  - Two related bugs in the data-feed adapters, both surfaced by the coverage push.
- **fix(carbon_calculator)** all four sustainability grades reachable across realistic routes (`f9d0584`)
  - EEDI grades B and C were arithmetically unreachable with the prior constants. The formula `100 - 77.85 * transit_days` (derived from _BENCHMARK=0.05 and a /2 divisor) meant integer days >= 1 always landed at eedi <= 22.15 -> grade D; onl...
- **fix(normalizer)** three silent empty-frame bugs masked by broad try/except (`dfe991b`)
  - Three real bugs in data/normalizer.py found by the coverage push, each producing an empty DataFrame instead of the properly normalized one. Production callers silently received zero rows.
- **fix(alert_engine_v2)** accept BSXRLM as primary BDI key — real FRED series ID (`2e33259`)
  - The alert engine's _bdi_series helper only looked for keys BDIY / BDI / bdi and explicitly never BSXRLM. But BSXRLM is the actual FRED series ID for the Baltic Dry Index (see reference_fpdf_and_fred_gotchas memory + the existing processi...

### 🎨 UI

- **ui** alert analytics panel + 6-kind channel selector + digest_mode toggle + report share-link buttons (`e9df1f8`)
  - Four UI wirings landing together — each surfaces engine work that was already shipped but was UI-less. No new tests (the existing smoke suite covers the import + render paths).
- **ui(alerts)** kind selector + test-channel button + URL/email validation (`5f255a5`)
  - Extends the delivery channels panel (commit e95c305) so users can actually configure email channels from the UI instead of having to hand-INSERT into the SQLite delivery_channels table. Adds a per-row test button and inline validation th...
- **ui(telemetry)** LLM Usage panel in tab_data_health (`517011a`)
  - Surfaces the cost-telemetry layer (commit d888363) in the existing data-quality + observability dashboard. Users can now answer 'what have I spent on Anthropic this week' without leaving the app.
- **ui(commentary)** wire build_commentary into 4 more tabs (`ae46122`)
  - Spreads the per-tab editorial commentary (commit 57f0559) from the single reference tab (tab_overview) to four more high-traffic tabs.
- **ui(alerts)** delivery channels panel + 'send pending' button (`e95c305`)
  - Wires the engine.alert_delivery layer (commit c9b7664) into the alerts tab. Users can now add/edit/delete Slack webhook channels from the UI and trigger 'deliver pending alerts from the last 24h' on demand.

### ✅ Tests

- **test** smoke coverage for all 64 UI tabs (192 tests) (`13060fe`)
  - Every ui/tab_*.py module is now covered by three smoke tests: 1. Module imports cleanly (catches broken engine/processing refactors that ripple into UI imports). 2. render() does not raise on empty/None inputs (catches missing-key bugs,...
- **test** coverage for routes/route_registry + routes/rate_estimator (50 tests) (`5f7d658`)
  - Both routes modules now have full coverage. One file covers both because they are tightly related (the rate estimator iterates over the route registry).
- **test** coverage for six big report + AI modules (445 tests) (`1b33256`)
  - Six large modules — 445 new tests, 2807 passing overall (was 2367) + 1 skipped (excel_export). Built in parallel via 6 sub-agents.
- **test** coverage for helpers + log_reader + report_history + digest_builder (172 tests) (`1a3a857`)
  - Four utils modules — 172 new tests, 2367 passing overall (was 2185). Built in parallel via 4 sub-agents.
- **test** coverage for alphavantage_feed + aisstream_feed + carrier_intelligence (185 tests) (`218a8d9`)
  - Three more data modules — 185 new tests, 2185 passing overall (was ~2000). Built in parallel via 3 sub-agents; mocks-only, zero network calls.
- **test** coverage for six data-feed adapters (259 tests) (`1f03a15`)
  - Six more data feeds — 259 new tests, 1997 passing overall (was 1738). Built in parallel via 6 sub-agents; mocks-only, zero network calls.
- **test** coverage for fred_feed + stock_feed + worldbank_feed + currency_feed (131 tests) (`79bcdc4`)
  - Four data-feed adapters — 131 new tests, 1738 passing overall. Built in parallel via 4 sub-agents; mocks-only, zero network calls.
- **test** coverage for carbon_calculator + fleet_tracker + normalizer + cache_manager (162 tests) (`56bf09b`)
  - Four more modules — 162 new tests, 1601 passing overall (was 1439). Built in parallel via 4 sub-agents; one batch commit.
- **test** coverage for equipment_tracker + backtest_engine + rate_forecaster + trade_finance (189 tests) (`4a9f20b`)
  - Four more processing modules — 189 new tests, 1439 passing overall (was 1250). Built in parallel via 4 sub-agents; commit is a single batch so suite stays cohesive.
- **test** coverage for leading_indicators + alert_engine_v2 + cargo_analyzer + eta_predictor (147 tests) (`fe77640`)
  - Four more processing/engine modules — 147 new tests, 1249 passing overall (was 1102).
- **test** coverage for forecaster + rate_analytics + commodity_shipping + company_profiler (87 tests) (`0fba75b`)
  - Four more processing modules — 87 new tests, 1102 passing overall (was 1015).
- **test** coverage for sector_dashboard + inventory_analyzer + freight_volatility + regime_detector (108 tests) (`d8d00ef`)
  - Four previously-untested processing modules — 108 new tests, 1015 passing overall (was 907).
- **test** processing coverage push #2 — vulnerability_scorer + supply_chain_risk + risk_monitor (42 tests) (`759a0a1`)
  - Continues the processing coverage push. Three more untested modules from the risk family — all with clear, testable scoring contracts.

### 📦 Other

- **deploy** docker compose runs the Streamlit app + worker as two containers sharing the SQLite volume (`650975d`)
  - The Dockerfile (commit 17b3e20) builds an image that runs the Streamlit app. The scheduler worker (commit 29b26c9) needs to run in a separate process — previously the deploy story was 'put it on host cron'. This adds a docker-compose.yml...
- **ci** GitHub Actions workflow runs the full pytest suite on every push + PR (`50571ea`)
  - Locks in the 3181-test safety net so a regression cannot sneak in unobserved. Previously the suite was a local-only investment.

## 2026-05-20

### 🐛 Fixes

- **fix** alphavantage rate-limiter is thread-safe (`a5567e3`)
  - `_rate_limited_get` did read-check-sleep-write on the module-level `_last_request_time` without a lock. Under Streamlit's threaded execution model (each worker thread reruns the script), two concurrent callers could:
- **fix** instance RNG for tab_eta and tab_routes — no more random.seed() globals (`97d7240`)
  - Mirror of the earlier `tab_portfolio` fix (commit 7c739ff) but for Python's `random` module: four `random.seed(N)` + `random.gauss/uniform/...` sites replaced with instance-scoped `random.Random(N)` so the process-wide random state is ne...
- **fix** cache & persistence paths anchor to project root, not CWD (`c409bb0`)
  - Four files defined cache / persistence Paths as relative strings:
- **fix** drop @st.cache_data from fetch_all_port_vessels — unhashable args (`18f28ea`)
  - `fetch_all_port_vessels(ports_cfg: list[dict])` was decorated with @st.cache_data, but `list[dict]` is unhashable. Streamlit's behavior on unhashable params is version-dependent: recent versions raise UnhashableParamError, older ones fal...
- **fix** timezone correctness — UTC label was showing local time (`932cdf1`)
  - Two small but real bugs in datetime handling:
- **fix** congestion forecaster uses None sentinel, not magic 0.45 (`a21fe50`)
  - `forecast_congestion_advanced` initialized `current = 0.45` and later checked `if current == 0.45` to decide whether to fall back to the last historical record. But `_clamp(vessel_count / hist_max)` can legitimately compute to exactly 0....
- **fix** tab_portfolio uses instance RNG, not numpy's global state (`7c739ff`)
  - `np.random.seed(N)` followed by `np.random.normal(...)` mutates numpy's process-wide RNG state. Under Streamlit's threaded execution model, two tabs rendering concurrently could step on each other's seeds; anywhere else in the process th...
- **fix** stable_hash() for all seeded synthetic data — 17 sites (`789518f`)
  - Python's built-in `hash()` is salted per process (PYTHONHASHSEED defaults to random in Python 3.3+), so any code using `hash(string_key)` to seed synthetic data was silently producing DIFFERENT data for the same input across processes:
- **fix** rate_forecaster cache key fingerprints input data (`c76c766`)
  - The in-process forecast cache was keyed only by route_id, which meant two callers feeding the same route under different histories would silently share a forecast — and a route whose underlying rate data updated mid-window would keep ser...
- **fix** backtester positional cols + 4 LOW audit findings (`bdaeba1`)
  - Closes out the audit's remaining findings.

### 🎨 UI

- **ui** PDF export buttons on every Phase-4 tab (`1600d42`)
  - Cross-cutting follow-on to the view_export commit (61387ff). Until now only tab_briefing had the "Export this view" button; this commit wires it into the other four new Phase-4 tabs so the feature is genuinely on every analytical surface.
- **ui** data-SLA dashboard in tab_data_health (`1897b39`)
  - Second Phase-5 deliverable. Adds an SLA compliance section between the Source Catalog and Cache & Credentials movements of tab_data_health.
- **ui+utils** view_export — per-tab "Export this view" PDF (`61387ff`)
  - Final Phase-4 item. Generic per-tab PDF export. Any tab assembles a ViewSnapshot from its already-computed content; build_view_pdf returns the bytes for an st.download_button.
- **ui+state** cross-tab filter bar with apply helpers (`370e4c3`)
  - Sixth Phase-4 deliverable. Single horizontal strip rendered above every section's tabs, persisting through state/session.py so any tab sees the user's current narrowing without re-asking.
- **ui** tab_nowcast — Phase-4 Trade Nowcast (`0032746`)
  - Fifth Phase-4 tab. UI for the existing processing/leading_indicators.py machinery — no new model code, pure synthesis of the analytical pieces already in place.
- **ui** tab_briefing — Phase-4 Daily Briefing tab (`530b8ec`)
  - Second Phase-4 tab. Dedicated single-screen surface for engine.narration_engine.generate_daily_narration (shipped in commit 140a586). A miniature lives in tab_overview as a panel (commit 083ad8b); this tab gives the briefing room to brea...
- **ui** tab_idea_engine — Phase-4 Signal-to-Trade Ideas synthesis (`f5f4873`)
  - First Phase-4 tab. Single screen synthesizing the Phase-3 infrastructure shipped this session into a ranked trade-ideas dashboard. Wired as the 6th tab in the Disruption Alpha section (after Equity Signals).
- **ui** wire narration_engine into tab_overview as Daily Briefing (`083ad8b`)
  - Adds a "Daily Briefing" panel to the Overview tab, between the page header and the Market Verdict section. Calls engine.narration_engine.generate_daily_narration (just landed in commit 140a586) with platform-computed signals and renders...
- **ui** alert-rule editor UI on engine/alert_engine_v2 (`205f0bd`)
  - Phase-3 roadmap line. The tab_alerts.py "Rules Management" section already had a read-only summary + enable/disable toggles, but no way to edit a rule's fields, no way to delete a rule, and no persistence — rules reset on every Streamlit...
- **ui** wire portfolio_optimizer into tab_portfolio — Optimization Lab (`a493458`)
  - Adds a new "Optimization Lab" section to the Portfolio Tracker tab, between Risk and Factor Attribution. Surfaces all four methods from engine/portfolio_optimizer.py (just landed in commit 9b3b510) directly in the UI.
- **ui** wire scenarios catalog into sidebar + tab_scenarios demo (`ee4d6e6`)
  - Makes state/scenarios.py visible end-to-end. Two integration points landed together so users can see the infrastructure in action.
- **ui** wire fleet_utilization into tab_voyage_tracker (`e27a271`)
  - Adds a "Fleet Utilization" section to the Voyage Tracker tab, between the existing fleet KPI strip and the vessel search. Surfaces the model from engine/fleet_utilization.py (just landed in commit 14570f5) directly in the UI.
- **ui** wire congestion_rate_lag into tab_congestion (`9c568d1`)
  - Adds a new "Congestion → Rate Lag Discovery" section to the Port Congestion tab, between the existing rate-impact scatter and the efficiency benchmarks. Surfaces the lag model from processing/congestion_rate_lag.py (just landed in commit...

### 📚 Docs

- **docs** refresh DISRUPTION_ALPHA + add MODELS.md methodology reference (`66cc315`)
  - DISRUPTION_ALPHA.md refreshed to reflect the deepened cascade — real per-route cargo shares via cargo_analyzer.get_route_cargo_mix, per-driver _CONVICTION_WEIGHT_SETS (each ∑=1.0, named in supporting_signals), and mean-reversion-aware vu...

### ✅ Tests

- **test** processing coverage push — seasonal + monte_carlo + options_screener (59 tests) (`8ab0c57`)
  - Continues the coverage push from engine into processing. Three modules picked by usage × untestedness × surface size — same selection methodology as the engine push.
- **test** coverage push #4 — convergence_tracker + scorer (40 tests) (`fa554e1`)
  - Closes the engine-coverage push from this session. Adds tests for the last two substantial untested engine modules.
- **test** coverage push #3 — correlator (20 tests) (`2deaee7`)
  - Continuing the engine-coverage push. Covers the 262-line correlator module which was previously untested.
- **test** coverage push #2 — momentum_ranker (25 tests) (`ec487fc`)
  - Continuing the engine-coverage push from commit 6ecc888. Covers the 406-line momentum_ranker module which was previously untested.
- **test** coverage push — 55 tests across 3 previously-untested engine modules (`6ecc888`)
  - Phase-5 coverage push. Targets the highest-leverage untested engine modules by lines × callers, picked from a usage audit:
- **test** deflake test_rate_signal_is_volatility_scaled (`edcd19a`)
  - The synthetic-history helper seeded its RNG with `abs(hash(rid)) % (2**32)`. Python hashes are salted per process (PYTHONHASHSEED defaults to random), so every Python invocation drew a different noise pattern. The volatility-scaling asse...
- **test** silence spurious Accelerate-BLAS matmul warnings (`5610123`)
  - macOS Accelerate's SIMD path sets FPE flags on register-level arithmetic even when the user-visible matmul output is mathematically finite — numpy 2.x dutifully reports these as RuntimeWarnings (numpy/numpy#27282). Verified spurious here...
- **test** hardening round 2 — lock-in coverage + integration test + 2 refinements (`c92dfd3`)
  - 3 new lock-in test files for the previously-audit-fixed helpers: - tests/test_index_tracker.py (190 lines) — pins _safe_series correctly selecting the rate/value column by name, not iloc[:, 0] (date). - tests/test_market_commentary.py (1...

### 📦 Other

- **observability** rotated logging + in-app log viewer (`a846c80`)
  - Third Phase-5 deliverable. Replaces the default "loguru writes to stderr forever" with rotated file logging and surfaces a live tail inside tab_data_health.
- **deploy** Dockerfile + .dockerignore + DEPLOYMENT.md (`17b3e20`)
  - First Phase-5 deliverable. Closes the "Streamlit Community Cloud + Fly.io Dockerfile" roadmap line.
- **models+ui** risk_lab + tab_risk_lab — Phase-4 VaR / Stress / Regime (`128bbfb`)
  - Fourth Phase-4 tab. New analytical module + UI surfacing three things the existing tab_risk_matrix doesn't cover.
- **models+ui** convergence_analyzer + tab_convergence — Phase-4 Convergence Lab (`e2a9e73`)
  - Third Phase-4 tab. Adds a new analytical module and the UI that surfaces it.
- **models** carrier_factor_model — factor attribution decomposition (`0e89d94`)
  - Closes the final Phase-3 line. The module already shipped the factor-fit + residual z-score + walk-forward backtest + tab_portfolio UI wiring (commits before this session). What was missing: a way for analysts to answer "why is ZIM up 8%...
- **models** port_demand_forecaster — add walk-forward backtest harness (`40c5bb2`)
  - Last unchecked Phase-3 line ("processing/port_demand_forecaster.py (upgrade with backtest)"). The existing signal-based forecaster (forecast_port_demand, forecast_all_ports, …) stays untouched — it's a snapshot-style model that computes...
- **models** narration_engine — daily LLM briefing path (Claude API) (`140a586`)
  - Phase-3 roadmap line "engine/narration_engine.py wired to Claude API (cached daily)". The existing 1158-line rule-based NarrationEngine stays untouched as the workhorse for per-route / per-port commentary; this commit appends a new path:...
- **models** engine/portfolio_optimizer.py — 4 methods + walk-forward backtest (`9b3b510`)
  - Phase-3 roadmap line. Convex portfolio optimization over the shipping equity universe, with four canonical methods all subject to long-only + weight-cap constraints. Walk-forward backtest required by docs/ROADMAP principle 5 is included.
- **state** scenarios.py + scenario overlay mixin (`541eb2b`)
  - Phase-3 infrastructure: lets any tab answer "what would this number look like if X happened?" against a shared, named set of canonical scenarios — without each tab reinventing shock-application logic.
- **models** engine/fleet_utilization.py — 4-component utilization composite (`14570f5`)
  - Phase-3 roadmap line. Derives a [0, 1] fleet-utilization score from the modeled voyage fleet and exposes it as a leading indicator for freight rates. Higher utilization ⇒ tighter capacity ⇒ bullish for rates.
- **models** processing/congestion_rate_lag.py — port-congestion → rate lag (`5370daa`)
  - Quantifies the lag between port-congestion changes and subsequent freight- rate changes on the lanes that port serves. Hits the Phase-3 roadmap line "processing/congestion_rate_lag.py".
- **cleanup** remove 35 dead modules (~19,500 lines) (`5a042aa`)
  - Mass deletion of modules that had ZERO production imports AND zero tests AND zero non-trivial doc references. These were verified unreferenced via a definitive grep-everywhere sweep against the whole repo (every .py file plus all docs)....
- **hygiene** import-time weight invariants via ValueError, not assert (`4b00570`)
  - Four modules guarded their blend weights with `assert abs(sum - 1.0) < eps` at import time. Under `python -O` assert statements are stripped, so the invariant would silently disappear and a future mis-edit of the weight constants would s...

## 2026-05-19

### 🐛 Fixes

- **fix** MEDIUM audit findings — BDI key, MC seed, two more positional bugs (`a7c6edf`)
  - - engine/alert_engine.py / alert_engine_v2.py / narration_engine.py: the BDI lookup was `macro_data.get("BDI") or macro_data.get("bdi")`, which (a) ignored the FRED canonical key "BDIY" (so the alert never fired from live data) and (b) r...
- **fix** 3 helpers selected `date` (column 0) instead of the rate/value column (`511162c`)
  - Same pattern as the rate_analytics fix — three helper functions positionally grabbed column 0 of a freight or FRED frame, which is `date`. The downstream float() / pct_change() then hit Timedelta, was caught silently, and three user-faci...
- **fix** rate_analytics._safe_series selects the rate column by name (`adc6f72`)
  - _safe_series did val.iloc[:, 0] — column 0 of a freight frame is `date`, not the rate — so compute_rate_regime hit float(Timedelta) and the Rate Analytics tab degraded to a caught st.error. Latent all along: the old single-row synthetic...

### 🎨 UI

- **ui** polish all 50 tabs across 9 sections (`9ffb162`)
  - Section dividers and headers, consistent column gaps, modebar-free charts with stable keys, sharpened empty states and micro-labels. Functionality, data wiring, render() signatures and try/except blocks unchanged; no new colors, no rethe...
- **ui** redesign sidebar navigation (`4d31b24`)
  - Masthead brand block and "Core" / "Analysis & Risk" cluster labels group the nav sections; the active state carries the per-section accent via --sec-accent / --sec-bg CSS vars, with refined button hover/active styling.
- **ui** elevate design system — depth, chart theme, motion (`b47ffcb`)
  - Raise execution quality with no retheme (dark steel-blue WSJ identity unchanged): - :root tokens: layered shadows, --edge-hi highlight, eased curves, --glow-accent - cards/metrics/expanders/buttons gain box-shadow + hover-lift; app canva...

### ✅ Tests

- **test** add unit coverage for 7 previously-untested modules (`6977463`)
  - Deterministic, network-free tests matching the existing suite style for modules that had no test file: chokepoint_analyzer, scenario_analyzer, supply_chain_health, port_demand_forecaster, weather_risk, alert_engine, routes/optimizer. ~15...

### 📦 Other

- **models** validate the platform's real signals against forward outcomes (`67bea72`)
  - New processing/signal_validation.py replays the cascade's ranked EquityIdea list and the commodity signals against forward returns over the synthetic price history — transparent arithmetic, no fitted ML. Produces a track record: per-sign...
- **models** deepen the cascade scorer — transparency preserved (`a0ce009`)
  - - disruption_cascade.py: conviction is no longer one fixed weight set — _CONVICTION_WEIGHT_SETS holds per-driver sets (chokepoint-driven ideas up-weight cascade magnitude, rate-driven ideas up-weight signal agreement), each hand-authored...
- **models** sharpen disruption-stress forecasting (`9691cbe`)
  - - congestion_predictor.py: per-port mean-reversion baselines (_PORT_BASELINE) replace the fixed 0.5 target; forecasts now carry confidence bands (volatility-scaled, widening with horizon); macro pressure is magnitude- aware — scaled by B...
- **models** realistic synthetic data — correlated voyages, time-series feeds (`c523272`)
  - The platform runs on synthetic fallbacks whenever live APIs are rate- limited, so their realism is the realism users see. Five generators upgraded; every output schema is byte-for-byte unchanged. - voyage_dataset.py: a per-route congesti...
- **models** mean-reverting Monte Carlo + seasonal-aware forecaster (`182f5cf`)
  - Two rate-forecasting engines made genuinely sharper: - monte_carlo.py: Geometric Brownian Motion (a pure random walk that drifts unboundedly over long horizons) replaced with an Ornstein- Uhlenbeck mean-reverting process in log-space, pl...

## 2026-05-18

### ✨ Features

- **feat** add Disruption Alpha section — vessel voyages to equity signals (`b2493d1`)
  - New sidebar section with 5 tabs (Voyage Tracker, Disruption Radar, Macro Projection, Supply Linkage, Equity Signals) tracing shipping disruption through detection, forecasting, macro projection, commodity exposure, and ranked fully-trace...

### 🐛 Fixes

- **fix** eliminate platform-wide pre-existing bugs (`02a181b`)
  - Fixes a broad class of latent bugs surfaced while building Disruption Alpha: 8-digit-hex Plotly colors, ~24 render() signature mismatches with app.py, feedparser RSS fetches with no network timeout (a dead feed could hang the whole app),...

### 🎨 UI

- **ui** elevate the design system to flagship "Refined Steel" polish (`f1bd812`)
  - Layered shadow system, hover-lift micro-interactions, refined motion easing, app-canvas depth and selection styling in ui/styles.py — cascades to all 54 tabs. Portfolio-piece visual polish for the front-door Overview and Scorecard tabs.
- **ui** remove unused imports and dead code across tabs (`52f12f2`)
  - Swept with ruff F-rules — 16 unused imports + 2 dead local variables removed. Also corrected a stale comment in tab_geopolitical.

### 📚 Docs

- **docs** regenerate audit-baseline.csv (`7ddc9d5`)
- **docs** regenerate audit-baseline.csv after tab_markets style-block cleanup (`c15b220`)
- **docs** regenerate audit-baseline.csv after eighth migration wave (`86f32ca`)

### ✅ Tests

- **test** add Disruption Alpha module tests; harden styles audit (`907904b`)
  - 92 pure-function tests across 5 files for the Disruption Alpha modules (determinism, score bounds, weight sums, graceful degradation on empty input). Hardens tools/styles_audit.py to detect inline style= on any HTML tag, closing the rela...

### 📦 Other

- **tab_markets** replace embedded <style> block with wsj_market_table (`096031f`)
- **tab_chokepoints** finish design-system migration (`aa38e87`)
- **tab_report** finish design-system migration (`0d471da`)
- **tab_weather** finish design-system migration (`2d905e6`)
- **tab_network** finish design-system migration (`d6ae148`)
- **tab_overview** finish design-system migration (`3e5a4d8`)
- **tab_portfolio** finish design-system migration (`4593226`)
- **tab_booking** finish design-system migration (`4c859c2`)
- **tab_fundamentals** finish design-system migration (`b55be0e`)
- **tab_indices** finish design-system migration (`f5f62f3`)
- **tab_monte_carlo** finish design-system migration (`513c9f3`)
- **tab_options** finish design-system migration (`34ebb6e`)
- **tab_port_demand** finish design-system migration (`6e90a95`)
- **tab_emerging_routes** finish design-system migration (`d6579b0`)
- **tab_visibility** finish design-system migration (`78a942b`)
- **tab_equipment** finish design-system migration (`09d9f0e`)

## 2026-05-17

### 📚 Docs

- **docs** regenerate audit-baseline.csv after sixth & seventh migration waves (`a7d14a4`)

### 📦 Other

- **tab_trade_flows** full migration to design system (`a57cd5d`)
- **tab_sustainability** full migration to design system (`5fb7f06`)
- **tab_commentary** full migration to design system (`8a70d2b`)
- **tab_bellwethers** full migration to design system (`36f5821`)
- **tab_alerts** full migration to design system (`d232327`)
- **tab_news** drop redundant inline styles on wsj-news-text (`0597b7f`)
- **tab_markets** drop redundant inline style on wsj-card (`bb2df90`)
- **tab_vessel_map** full migration to design system (`f07e5d7`)
- **tab_risk_matrix** full migration to design system (`1545f3e`)
- **tab_geopolitical** full migration to design system (`8d6ae10`)
- **tab_data_health** full migration to design system (`f2215d8`)
- **tab_backtest** full migration to design system (`41d27c7`)

## 2026-05-16

### 📚 Docs

- **docs** regenerate audit-baseline.csv after fifth migration wave (`2fbbcc0`)

### 📦 Other

- **tab_news** full migration to design system (`52869b2`)
- **tab_markets** full migration to design system (`7c5a751`)
- **tab_macro** full migration to design system (`4f971a7`)
- **tab_intermodal** full migration to design system (`530f0fa`)
- **tab_congestion** full migration to design system (`598d5a1`)
- **tab_sector** full migration to design system (`54d5dfb`)
- **tab_port_monitor** full migration to design system (`5de46e0`)
- **tab_deep_dive** full migration to design system (`241e6f6`)
- **tab_cycle** full migration to design system (`423c34f`)
- **tab_cargo** full migration to design system (`5061461`)
- **tab_attribution** full migration to design system (`bedc99d`)

## 2026-05-11

### 📚 Docs

- **docs** regenerate audit-baseline.csv after fourth migration wave (`66e2624`)
  - 7 more tabs migrated this wave:

### 📦 Other

- **tab_finance** full migration (retry after partial agent work) (`32713a4`)
  - Agent-prepared migration on the second attempt — the first rate-limited out at ~50% complete, this run finished the remaining 38 inline divs.
- **tab_live_feed** full migration to design system (`b129935`)
  - Agent-prepared migration. page_header (LIVE FEED), section_header x7, metric_card_row x1, wsj_market_table x2, insight_card_html x2 (empty-state + per-alert), ticker_tape_html x1 (replaces bespoke 3-div @keyframes scroll-left block), app...
- **tab_assistant** full migration + last palette redecl removed (`e722c23`)
  - Agent-prepared migration. page_header (ASSISTANT, no icon), metric_card_row x1 (4 assistant-context KPIs), section_header x5, wsj_market_table x3 (data-context, how-to-use, active-LONG signals), source_footer x2, status_badge x5 (one per...
- **tab_bunker** full migration to design system (retry after rate limit) (`fd26d25`)
  - Agent-prepared migration. All 7 sub-renders converted: page_header (BUNKER, no icon), source_footer x7 (one per chart/table/KPI block), insight_card_html x3 (slow-steaming rule, 4 hedging strategies, hedging rule), metric_card_row x4 (da...
- **tab_weather** full migration to design system (`0bacd33`)
  - Agent-prepared migration. page_header (WEATHER, no icon), section_header x extra ("Live Weather Alert" replaces the gradient alert div), metric_card_row in KPIs + new 2-col in _render_ice_route, wsj_market_table x3 (three caption-row use...
- **tab_network** full migration to design system (`b366a9e`)
  - Agent-prepared migration. page_header (NETWORK, no icon), section_header x5, metric_card_row x1, wsj_market_table x4 (incl. stress-test cards collapsed to one 7-column table), apply_dark_layout x2, insight_card_html x1 (methodology block...
- **tab_eta** full migration to design system (`0b04539`)
  - Agent-prepared migration. page_header (ETA, dropped icon), 7 source_footer callsites (one per sub-section: KPI, voyage tracker, ETA calculator, delay analysis, reliability trends, weather forecast, port queue). New module-level _ETA_SOUR...

## 2026-05-10

### 📚 Docs

- **docs** regenerate audit-baseline.csv after third migration wave (`8fb9012`)
  - 8 more tabs migrated this wave:
- **docs** regenerate audit-baseline.csv after second migration wave (`f93ac99`)
  - 12 more tabs migrated to the shared design system in this wave:

### 📦 Other

- **tab_visibility** full migration to design system (`2c03235`)
  - Agent-prepared migration. page_header (VISIBILITY, dropped icon), section_header x6 (no icon param), section_divider x3 preserved, metric_card_row x2 (hero KPIs + new milestone-progress strip), wsj_market_table x4 (visibility, exceptions...
- **tab_fundamentals** full migration to design system (`f5980ed`)
  - Agent-prepared migration. source_footer x5 (screening, valuation, deep-dive expander, dividend tracker, relative-value heatmap), wsj_market_table x1 new (Key Ratios block replacing a 28-line inline grid card), metric_card_row / apply_dar...
- **tab_indices** full migration to design system (`64f16c8`)
  - Agent-prepared migration. All 8 sub-renders converted: page_header (INDICES, dropped icon param), section_header x7 (pre-existing), metric_card_row x4 (index dashboard groups, BDI components, BDI vs 5Y context, FFA scenario), wsj_market_...
- **tab_booking** full migration to design system (`6d057dc`)
  - Agent-prepared migration. page_header (BOOKING, dropped icon param), insight_card_html x1 (booking-window narrative), metric_card_row x5 (12-week calendar in two rows of 6, spot-rate-alert status panel), wsj_market_table x4 (near-thresho...
- **tab_supply_chain** full migration to design system (`280c4b1`)
  - Agent-prepared migration. All 8 sections converted: page_header (SUPPLY CHAIN), section_header x10, section_divider x7, metric_card_row x3 (deltas, I/S signals, JIT/JIC summary), wsj_market_table x7 (SCHI sub-scores, disruptions, nearsho...
- **tab_scenarios** full migration to design system (`7474252`)
  - Agent-prepared migration. page_header (SCENARIOS), section_header x8, metric_card_row x4, wsj_market_table x5, source_footer x5, apply_dark_layout x1 (already in place). 4 new DataSource provenance constants (_MACRO_SRC, _EVENT_SRC, _MC_...
- **tab_scorecard** full migration to design system (`ed500e4`)
  - Agent-prepared migration. metric_card_row x3 (exec summary 4-card, winner/loser 3-card; legacy category bar kept), insight_card_html x6 (1 exec summary outlook + 5 forward predictions). section_header x4, source_footer x7. page_header: d...
- **tab_overview** full migration to design system (`ff84e35`)
  - Agent-prepared migration. All 9 sub-renders converted: page_header (DASHBOARD), section_header x9, metric_card_row x2 (KPI strip + cold-start onboarding), wsj_market_table x7 (Market Pulse, Signal Conviction matrix, Top Signals, Risk & A...
- **tab_finance** Phase B + Z — partial agent migration + cleanup (`795e8c0`)
  - Agent ran out of usage limit halfway through the migration; majority of inline divs were converted before the cap. The remaining work was finished in this commit: 3 _kpi_card columns blocks (SCF section, de-dollarization 3-col, sanctions...
- **tab_ecommerce** Phase B + Z — full migration to design system (`1721384`)
  - Agent-prepared migration. All 8 sub-renders + main render() converted: page_header (E-COMMERCE), section_header x8, metric_card_row x5, wsj_market_table x4 (platform, de-minimis impacts, b2c/b2b, returns), insight_card_html x14 (KPI cont...
- **tab_report** Phase B + Z — full migration to design system (`4bc85ea`)
  - Agent-prepared migration. All 8 sub-renders converted: page_header (REPORT), section_header x9, metric_card_row x2 (hero, preview), wsj_market_table x3 (history, data sources, API config), insight_card_html x4 (engine offline, generation...
- **tab_carriers** Phase B + Z — full migration to design system (`af377fc`)
  - Agent-prepared migration (rate-limit terminated the report but the file edits completed). page_header (CARRIERS), section_header, metric_card_row x5, insight_card_html x3, wsj_market_table x7. Inline divs: 39 → 0. ruff clean. pytest 68/68.
- **tab_compliance** Phase B + Z — full migration to design system (`5cc0ef7`)
  - Agent-prepared migration (rate-limit terminated the report but the file edits completed). page_header (COMPLIANCE), section_header, metric_card_row x4, insight_card_html x12, wsj_market_table x11. Inline divs: 49 → 0. ruff clean. pytest...
- **tab_routes** Phase B + Z — full migration to design system (`e2b0c31`)
  - Agent-prepared migration. All 7 sections converted: page_header (ROUTES), section_header x7 (replaces section_divider), metric_card_row x3 (KPI row, gainers/losers, per-route stats), wsj_market_table x4 (League, ML featured forecasts, mo...
- **tab_chokepoints** Phase B + Z — full migration to design system (`48e477f`)
  - Agent-prepared migration. All 6 sections converted: page_header (CHOKEPOINTS), section_header x6 (status board, panama, suez, red sea monitor, rate premiums, historical), metric_card_row x4 (header KPI, panama, suez, insurance), wsj_mark...
- **tab_trade_war** Phase B + Z — full migration to design system (`c9f9f7e`)
  - Agent-prepared migration. All 8 sections converted: page_header (TRADE WAR), section_header x7, metric_card_row x4 (hero, nearshoring 5-col, history 4-col, scenario), insight_card_html x5 (3 in diversion-map losers/winners/emerging, 2 fr...
- **tab_options** Phase A + B + Z — full migration to design system (`3c8bdf3`)
  - Agent-prepared migration. Drop the 2 module palette redecls; import from ui.styles. All 8 sections converted: page_header (OPTIONS), section_header x6, metric_card_row x2 (Unusual Activity 5-card, Max Pain 3-card), insight_card_html (one...
- **tab_derivatives** Phase A + B + Z — full migration to design system (`3211b2a`)
  - Agent-prepared migration. Drop 5 module palette redecls/locals (C_BG, C_SURFACE, C_PURPLE, C_TEAL, C_CYAN); import canonical constants from ui.styles. Keep C_TEAL = "#14b8a6" as a single documented tab-local accent.
- **tab_alpha** Phase B + Z — full migration to design system (`76f10d2`)
  - Agent-prepared migration. All 7 sub-renders converted: page_header (ALPHA badge), section_header x6, metric_card_row(columns=4) for hero KPIs, wsj_market_table x7 (conviction matrix, top signals, engine diagram, factor breakdown, live mo...
- **tab_portfolio** Phase B + Z — full migration to design system (`79ef0fa`)
  - Replace _render_hero inline div with page_header (PORTFOLIO badge). _render_summary_metrics → metric_card_row(columns=4). _render_risk_metrics → section_header + metric_card_row(columns=4) + source_footer (Monte Carlo provenance). _rende...

## 2026-05-09

### 📚 Docs

- **docs** regenerate audit-baseline.csv after design-system migration wave (`127641a`)
  - Six tabs migrated to the shared design system in this wave:

### 📦 Other

- **tab_fleet** Phase B + Z — sub-render migrations + cleanup (`070caad`)
  - Sub-render migrations (Phase B): - All 7 _section() callsites swap to ui.styles.section_header(). - 5-card KPI strip (_render_kpis) → metric_card_row(columns=5). - 4 _dark_layout() dict-mutation sites → apply_dark_layout(fig, ...) + foll...
- tab_emerging_routes / tab_port_demand / tab_monte_carlo: Phase Z (`63a2ef8`)
  - Drop the unused ui.styles imports left over after the agent-prepared Phase B commits. tab_monte_carlo also drops two unused locals (T and mu) flagged by ruff F841 — both were leftovers from earlier shock / GBM rewrites.
- **tab_monte_carlo** Phase A + B (agent-prepared) (`8089a58`)
  - Phase A complete: drop module palette redecls, import canonical constants from ui.styles, add _mono / _sans cell formatters, add page_header in render().
- **tab_port_demand** Phase A + B (agent-prepared) (`9942604`)
  - Phase A complete: drop module palette redecls, import canonical constants from ui.styles, add _mono / _sans cell formatters, add page_header in render().
- **tab_emerging_routes** Phase A + B (agent-prepared) (`dbc5721`)
  - Phase A complete: drop module palette redecls, import canonical constants from ui.styles, add _mono / _sans cell formatters, add page_header in render().
- **tab_fleet** Phase A — palette + page_header + cell formatters (`7ac001a`)
  - Drop the 14 module-level palette decls (the canonical 11 plus three local extensions C_PURPLE/C_CYAN/C_ORANGE). Import canonical constants from ui.styles. C_PURPLE call sites swap to C_CONV (same hex #7c6eaf); the unused C_CYAN and C_ORA...
- **tab_results** Phase Z — drop dead helpers, finalize cleanup (`18b51ec`)
  - Remove the now-unused local helpers replaced by ui.styles equivalents during Phase B: _card_wrap, _section_header, _kpi, _color_pct, _monthly_attr_html, _signal_log_html, _leaderboard_html, _instrument_table_html. Drop unused imports: ra...
- **tab_results** Phase B — sub-render migrations (`7165abf`)
  - All 8 sections in render() converted to design-system helpers. Local _section_header() calls swapped for ui.styles.section_header().
- **tab_results** Phase A — palette + page_header + cell formatters (`9e83410`)
  - Drop the 11 module-level C_* palette redeclarations and import the same constants from ui.styles (the canonical source of truth). Add the _mono / _sans cell formatters mirroring the pattern in ui/tab_rate_analytics.py and ui/tab_equipmen...

## 2026-05-08

### 📦 Other

- **tab_equipment** B-Z — drop dead helpers, finalize cleanup (`5196af6`)
  - Local helpers replaced by ui.styles equivalents during B-06 → B-12 are now removed: _RISK_COLOR (→ RISK_COLORS), _hex_to_rgb (only used by the removed helpers), _risk_badge (→ badge), _trend_badge (unused), _kpi_card (→ metric_card_row),...
- **tab_equipment** B-12 — refactor _render_cost_calculator (`c242097`)
  - KPI output row (Base, Repositioning, Adjusted Total, Rate Uplift) → metric_card_row(columns=4). Trade-imbalance / repositioning detail card (previously a single flex container with 4 inline divs) → second metric_card_row(columns=4) with...
- **tab_equipment** B-11 — refactor _render_lease_vs_own (`19bb00a`)
  - Both charts (cost dual-axis bar+line and break-even bars) switch from dark_layout dict-mutation to apply_dark_layout + update_layout. The right-column lease/own detail cards (5 container types) collapse from hand-built grid divs into ins...
- **tab_equipment** B-10 — refactor _render_reefer_section (`c94c847`)
  - Five-card KPI strip → metric_card_row(columns=5). Reefer utilization / lease-rate dual-axis chart and seasonal demand chart both switch from dark_layout dict-mutation to apply_dark_layout + update_layout. Right- column commodity cards (5...
- **tab_equipment** B-09 — refactor _render_enhanced_equipment_overview (`86d3b57`)
  - Largest sub-render in the tab (260 LOC). KPI hero strip → metric_card_row. Geo balance map, repositioning cost bar, and dwell-time bar all switch from dark_layout dict-mutation to apply_dark_layout(fig, ...) + update_layout for chart-spe...
- **tab_equipment** B-08 — refactor _render_age_distribution (`46c776c`)
  - Convert age-bracket detail cards to metric_card_row(columns=6) with sublabel carrying status + note. Replace dark_layout dict mutation with apply_dark_layout for both the donut and the volume/urgency dual-axis chart. Replace the bottom r...
- **tab_equipment** B-07 — refactor _render_shortage_alerts (`d9c4935`)
  - Replace per-alert inline card HTML with insight_card_html — score is the utilization fraction, action is mapped from risk (CRITICAL→Avoid, HIGH→Caution, MODERATE→Monitor, LOW→Watch), rationale is built from route + util + deficit + lease...
- **tab_equipment** B-06 — refactor _render_global_pool_overview (`ae09397`)
  - Replace hand-built KPI st.columns + _kpi_card calls with metric_card_row (both the top utilization strip and the fleet-growth strip at the bottom). Replace dark_layout dict mutation with apply_dark_layout(fig, ...) + follow-on update_lay...

## 2026-04-29

### 📦 Other

- **tab_equipment** B-02 / B-04 / B-05 batch — refactor balance_timeline, repositioning_costs, dwell_times (`e0a1886`)
  - Three subsection refactors landed in parallel by background agents, batched here as one commit for clean review. Adds the new equipment smoke test scaffold at the same time.
- **tab_equipment** B-01 — refactor _render_shortage_surplus_map to ui.styles helpers (`52fd75d`)
  - - Heatmap uses apply_dark_layout(fig) + per-chart fig.update_layout(...) overrides instead of building a layout dict by hand. - Risk legend uses badge(..., color=RISK_COLORS[r]) instead of local _risk_badge. - Per-region summary card use...
- **tab_equipment** Phase A — drop local palette, import from ui.styles, add page_header + cell formatters (`c16f636`)
  - Foundation for the wave-1 refactor of `tab_equipment.py`. Subsection refactors will follow in subsequent commits (one per `_render_*` function); this commit only sets up the prelude.

## 2026-04-22

### 📦 Other

- Promote tab_rate_analytics_refactored to canonical tab_rate_analytics (`655dcd5`)
  - The refactored tab was always meant to be the target, not a parallel file. This commit completes that promotion:
- Add statsmodels to requirements.txt for engine modules (`973b750`)
  - engine/carrier_factor_model.py and engine/cointegration.py both import statsmodels (OLS with HAC covariance, Johansen cointegration test). It was installed in the dev env but missing from requirements.txt, which broke CI on the first gre...
- Add CHANGELOG.md + release automation workflow (`3ce1430`)
  - CHANGELOG.md follows Keep-a-Changelog format. Kicks off with the 0.1.0-phase1 entry covering everything that landed in the Phase-1 foundation series.
- Migrate 40 tabs to shared design system (ui.styles imports, drop palette redecls) (`7d4bcdd`)
  - Every ui/tab_*.py now imports C_BG, C_SURFACE, C_CARD, C_BORDER, C_HIGH, C_MOD, C_LOW, C_ACCENT, C_TEXT, C_TEXT2, C_TEXT3 from ui.styles instead of redeclaring the palette locally. Ad-hoc page headers, KPI card rows, status chips, sectio...
- Add CI workflow, migration playbook, and reference refactored tab (`c6e8559`)
  - .github/workflows/ci.yml runs ruff + pytest on every push.
- Add pytest scaffold with 66 unit tests across foundation modules (`6e3a498`)
  - pytest.ini configures -x --timeout=30 --strict-markers and filters noisy deprecation warnings from pkg_resources / urllib3 / ui.components shim.
- Add cointegration (Johansen/ECM) and carrier factor model engines (`b55e455`)
  - engine/cointegration.py — Johansen trace test + error-correction model + half-life estimation for mean-reverting pairs. Surfaced via tab_indices to identify cointegrated freight-rate / macro pairs.
- Add typed SessionState/Filters schema in state/session.py (`a2ffc2c`)
  - Replaces the grab-bag of st.session_state[...] string keys with a dataclass- backed SessionState (and nested Filters) so tabs can share well-typed cross-cutting state — selected date range, active carriers, regime overlay, scenario overl...
- Add DataSource/DataSeries primitives + retrofit feeds with *_wrapped variants (`9aa5358`)
  - data/quality.py introduces DataSource (live | scraped | modeled | demo) and DataSeries (payload + source + freshness + health) so every chart/table can surface *where its number came from* via live_data_badge / source_footer.
- Consolidate design system into ui/styles.py + add audit tool (`ced2522`)
  - ui/styles.py absorbs stat_counter, mini_sparkline, gauge_ring, alert_banner, kpi_row, shipping_heat_bar, section_divider from ui/components.py and adds live_data_badge, regime_pill, spark_cell, source_footer, nav_section_button, page_hea...

## 2026-04-08

### 📦 Other

- Fix 6 st.markdown calls missing unsafe_allow_html=True (`d0e93b4`)
  - The global CSS injection (styles.py line 126-1164), app header (app.py line 561-633), nav breadcrumb, and 3 other multi-line HTML blocks were missing the unsafe_allow_html=True flag, causing the entire CSS and HTML to render as raw text...
- Revert st.html() back to st.markdown(unsafe_allow_html=True) (`98b1532`)
  - st.html() renders content in an isolated iframe, which breaks global CSS injection and structural HTML. Reverted all calls back to st.markdown(unsafe_allow_html=True) and fixed 11 calls where the flag had been accidentally stripped, caus...
- Fix raw HTML rendering: replace st.markdown(unsafe_allow_html) with st.html() across entire codebase (`879799c`)
  - Streamlit Cloud v1.56 renders raw HTML tags as visible text when using st.markdown(unsafe_allow_html=True) in certain contexts. Replaced all 905 occurrences across 58 files with st.html() which renders HTML directly without markdown pars...
- Add PostgreSQL schema for standalone web app (`fecf75a`)
  - 757-line schema with 30 tables, 5 materialized views, and helper functions covering: ports, routes, vessels, freight rates, macro indicators, trade flows, insights, alerts, news sentiment, users, watchlists, and data pipeline tracking. I...
- Add Global Trade Flows tab + fix dashboard HTML rendering (`45c3a5e`)
  - New Trade Flows tab (Trade & Macro section) with: - Global flow map showing cargo routes colored by dominant commodity - Sankey diagram: origin region → commodity → destination region - Route cargo breakdown with stacked bar visualizatio...
- Modern SaaS dashboard redesign + platform-wide UI polish (`855ff70`)
  - Rewrites the Overview dashboard from WSJ editorial style to a modern SaaS layout (status bar, KPI strip, 2-column body with market pulse, signal matrix, risk alerts, route opportunities, sparkline charts). Includes UI refinements across...

## 2026-03-22

### 📦 Other

- Institutional report overhaul: Bloomberg/GS quality PDF+HTML, enriched engine, 4 tab rewrites (`e9599ba`)
  - - investor_report_engine: mock data constants, ≥5 signals, ≥8 insights, richer BDI/WCI narratives, 5 recommendations with key_thesis bullets - investor_report_html: complete rewrite — white background, navy headers, gold rule, 8-section...
- Rebuild investor report PDF: full institutional Goldman Sachs / Morgan Stanley quality (`3c09c7b`)
  - Complete rewrite of investor_report_pdf.py (2,811 lines): - 16-page structured document with proper running headers/footers - Cover page: navy band, flash note classification, key metrics box, rating action box, executive summary - Table...
- Massive UI overhaul: 39 tabs rewritten + Bloomberg-style HTML dashboard (`9a63bd9`)
  - HTML Institutional Dashboard: - static/dashboard.html: full Bloomberg Terminal / Lloyd's List Intelligence quality standalone HTML page. Tailwind CSS, vanilla JS. Fixed header with 6 dropdown filters, 60/40 split layout, 53 real vessels...
- Platform buildout: Tier 1/2/3 features + institutional PDF redesign (`83eb613`)
  - New modules: - data/aisstream_feed.py: AIS vessel tracking with WebSocket stream - data/blank_sailing_feed.py: Blank sailing tracker with scraping layer - data/canal_feed.py: Panama/Suez canal wait times & transit data - data/carrier_int...
- Fix data source health indicators and Quick Navigation HTML rendering (`8640660`)
  - - app.py: change glob() to rglob() in _get_api_health() so it finds cache files in subdirectories (CacheManager stores at cache/{source}/slug.parquet, not flat in cache/) - tab_overview.py: rewrite chips_html as single-line concatenation...
- Add NewsAPI, Alpha Vantage, OECD, and IMF data integrations (`3052b99`)
  - New data feeds: - data/newsapi_feed.py: NewsAPI integration (150k+ sources), 3 shipping queries, URL dedup, rate-limit retry, graceful fallback when no key set - data/alphavantage_feed.py: Stock fundamentals (P/E, EPS, ROE, analyst targe...

## 2026-03-21

### 📦 Other

- Institutional-grade PDF report: Goldman design, matplotlib charts, full overhaul (`77bdda7`)
  - PDF Report (institutional masterpiece): - utils/pdf_charts.py: 10 publication-quality matplotlib charts at 180 DPI embedded in PDF — sentiment gauge with needle, freight momentum bars, alpha signal bubble scatter, dual-axis stock perform...
- Harden report system: fix runtime bugs, add history UI, add fpdf2 dep (`caabd7d`)
  - - investor_report_engine.py: fix composite_label NameError if step 7 fails, extract nested .format() from f-string (fragile pattern), wrap entire build_investor_report in outer try/except returning safe DEGRADED default on total failure...
- Fix InvestorReport attribute mismatch between engine and HTML builder (`0b30c5d`)
  - The HTML builder defined its own InvestorReport dataclass with different field names than the engine's version, causing AttributeError on report generation. Fixed by:
- Add investor-grade downloadable sentiment analysis report (`aa4cb00`)
  - New feature: full investor report system accessible from new "Reports" section in the sidebar. Generates an institutional-quality briefing document compiling live sentiment, alpha signals, freight rates, macro data, and AI recommendation...
- Fix rendering bugs and errors across 14 tabs (`24199c9`)
  - - tab_alpha: fix NameError on _C_TEXT/_C_TEXT2 (undefined prefixed vars) - tab_eta: add missing key= args to all st.plotly_chart calls (DuplicateWidgetID) - tab_cargo/booking: wrap bare render calls in try/except for error isolation - ta...

## 2026-03-20

### 📦 Other

- Massive visual overhaul: all 43 tabs fully enhanced (`e87f393`)
  - Complete rewrite of every tab in the shipping intelligence platform: - Every tab expanded with 8-15 new sections (hero dashboards, maps, heatmaps, charts, cards, timelines, simulators) - Consistent dark design system across all tabs - Al...

## 2026-03-19

### 📦 Other

- Final key sweep: 8 keyless expanders + 1 duplicate key fixed (`cd0e782`)
  - - tab_news.py: key="news_past_events_expander" - tab_ecommerce.py: key="ecommerce_platform_signals_expander", key="ecommerce_event_details_expander" - tab_port_demand.py: key="port_demand_comparison_expander" - tab_emerging_routes.py: ke...
- Massive quality pass: every tab polished, crash-proofed, and detailed (`2bbd416`)
  - ENGINE / DATA LAYER: - scorer.py: IndexError on empty insights list (result[0] fix) - optimizer.py + rate_estimator.py: NaN from .iloc[-1] without .dropna() - pair_signals.py: NaN correlation breaking entry_triggered logic - normalizer.p...
- Schindler sweep: 50+ crash fixes across engine, data layer, and all 43 tabs (`c8d1ed3`)
  - Critical engine fixes: - scorer.py: IndexError on empty insights list (result[0] with no results) - optimizer.py + rate_estimator.py: NaN propagation from .iloc[-1] without .dropna() - pair_signals.py: NaN correlation silently breaking e...
- Stability, caching, UX pass across all 43 tabs and data layer (`c053732`)
  - app.py: - Wrap tabs 0-6 (Overview–Supply Chain) in try/except — previously unprotected - Move Results tab import inside try block to catch import failures - Tab CSS: 12px font, 2px gap so 43 tabs fit comfortably - page_title → "Ship Trac...
- Fix StreamlitDuplicateElementId across all tabs + wrap Results in try/except (`87342f9`)
  - - tab_results.py: _render_signal_bar and _render_insight_timeline now accept chart_key param; render loop passes unique f"signal_bar_{i}_{j}" and f"insight_timeline_{i}" keys to eliminate duplicate widget ID crashes - app.py: wrap render...
- Fix matplotlib ImportError: replace background_gradient with applymap (`58b1a9d`)
  - pandas background_gradient requires matplotlib which is not installed on Streamlit Cloud. Replaced with applymap + inline rgba color logic in tab_port_demand.py, tab_routes.py, tab_macro.py.
- Expand to 43-tab platform: wire all second-wave tabs + new modules (`237ea46`)
  - New tabs added (tab35-tab42): - 🤖 Assistant (AI shipping chatbot, 18 question patterns) - 🚧 Chokepoints (9 global chokepoints, Red Sea tracker, closure simulator) - 🛡️ Compliance (10 sanctions regimes, dark fleet 600 vessels, route risk...
- Massive expansion: 35-tab intelligence platform with 50+ new modules (`86fa58c`)
  - New processing modules (24): - alpha_engine, cycle_timer, narration_engine - booking_optimizer, bunker_tracker, carrier_tracker - chokepoint_analyzer, congestion_history, derivatives_pricer - ecommerce_tracker, emerging_routes, equipment...
- Replace Comtrade+AISHub with zero-key alternatives (`d3346c7`)
  - - comtrade_feed.py: WITS API (World Bank trade DB) + WB merchandise fallback - ais_feed.py: IMF PortWatch + calibrated synthetic baselines (seasonal + noise) Both work with no API keys — all APIs now active
- Added Dev Container Folder (`d08befe`)
- Initial commit: Cargo Ship Container Tracker (`7e192ef`)
  - Full-stack shipping intelligence platform with 15 Streamlit tabs: - 3D globe, port demand, route analysis, decision engine - Monte Carlo simulation, sustainability, live feed, risk matrix - Fleet tracker, cargo breakdown, shipping indice...
