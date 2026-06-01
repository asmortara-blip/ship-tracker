# Scheduling — Daily Investor Briefing Worker

The investor briefing report can be built on a recurring schedule by the
standalone worker at `worker/scheduler.py`. The worker is invoked by an
external scheduler (unix `cron`, a Docker sibling container, or any CI
runner that supports cron syntax) — it does **not** run inside the
Streamlit process.

## What it does

Each run:

1. Loads the current freight/macro/stock/port/route/insight bundle via
   `worker.scheduler.load_data_bundle()` (same data sources as `app.py`,
   minus the Streamlit caching layer).
2. Builds an `InvestorReport` via
   `processing.investor_report_engine.build_investor_report(...)`.
3. Renders it to HTML via
   `utils.investor_report_html.render_investor_report_html(...)`.
4. Persists the HTML + a metadata row to SQLite via
   `utils.report_history.save_report(...)`.
5. **Optionally** (`--push` flag): for every enabled
   `engine.alert_delivery.DeliveryChannel`, calls
   `deliver_pending(channel, since=now - 24h)` so the last day's
   alerts flow out to Slack.

A populated `ReportJobResult` is printed as JSON. The process exits 0
on success, 1 on failure.

## Adding a cron entry

Run the worker every day at 07:00 UTC:

```cron
0 7 * * * cd /path/to/ship && /usr/bin/python3 -m worker.scheduler --push >> logs/scheduler.log 2>&1
```

Adjust the absolute path to the repo root and the python interpreter to
match your environment. Use `crontab -e` to install the line on a unix
host. The `>> logs/scheduler.log 2>&1` suffix captures both stdout and
stderr to a log file so you can audit past runs.

If you'd rather skip the outbound push and only persist the briefing
to the report history table, drop the `--push` flag:

```cron
0 7 * * * cd /path/to/ship && /usr/bin/python3 -m worker.scheduler >> logs/scheduler.log 2>&1
```

## Running it manually

To test the worker without waiting for cron:

```bash
cd /path/to/ship
python3 -m worker.scheduler           # build + save, no channel push
python3 -m worker.scheduler --push    # build + save + push to channels
```

The worker prints a JSON summary like:

```json
{
  "report_id": "5f3c9a2b-7e1f-4d80-9c42-d2c0c5e8f3a1",
  "file_path": "/path/to/ship/cache/reports/report_20260521_070003_5f3c9a2b.html",
  "success": true,
  "duration_s": 18.42,
  "error_msg": ""
}
```

## Environment variables

The worker reads the same configuration the Streamlit app does. The
data fetchers consult environment variables (or a `.env` file loaded
by `python-dotenv` at process start):

| Variable             | Purpose                                                    |
| -------------------- | ---------------------------------------------------------- |
| `FRED_API_KEY`       | FRED macro series (BDI, WTI, treasuries, etc.)             |
| `ALPHAVANTAGE_KEY`   | Optional — equity fundamentals                             |
| `NEWSAPI_KEY`        | Optional — news sentiment articles                         |
| `ANTHROPIC_API_KEY`  | Optional — only used if AI-narration features are enabled  |
| `COMTRADE_KEY`       | Optional — trade flow data                                 |

If a key is missing, the corresponding data source degrades to empty
and the report is marked `PARTIAL` or `DEGRADED` rather than failing.

For Slack push delivery you must have at least one
`DeliveryChannel` row in the SQLite `delivery_channels` table whose
`kind = "slack"` and whose `target` is a valid incoming-webhook URL.
The Streamlit alert-routing UI is the easiest way to add channels.

## The `--push` flag

| Flag                      | Behavior                                            |
| ------------------------- | --------------------------------------------------- |
| (none)                    | Build + render + save. No outbound notifications.   |
| `--push`                  | Also call `deliver_pending` (24h window) on every enabled channel. |

`--push` failures do **not** flip the job to failure — the report
itself is still considered shipped if it was built and saved. Delivery
errors are written to the loguru log only.

## Failure modes

The worker is intentionally crash-proof:

- `run_daily_briefing_job` never raises. Every exception is captured
  into `ReportJobResult.error_msg`.
- A missing data source degrades to an empty dict/list inside
  `load_data_bundle`. The engine returns a `DEGRADED`-quality report
  rather than crashing.
- `save_report` returning `None` (disk full, permission denied) is
  treated as a job failure (`success=False`) so cron picks up the
  non-zero exit status.

## Where reports go

- HTML files: `cache/reports/report_YYYYMMDD_HHMMSS_<short_id>.html`
- Index: SQLite `report_history` table inside `cache/ship_tracker.db`
- Retention: the most recent `utils.report_history.MAX_REPORTS` (30)
  are kept; older entries (and their files) are pruned by
  `_prune_old_reports` during each save.
