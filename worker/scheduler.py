"""worker/scheduler.py — daily investor briefing worker.

A standalone Python entry point that builds the daily investor briefing
PDF/HTML on a cron-style schedule, persists it via
``utils.report_history.save_report``, and optionally pushes a notice
through every enabled outbound ``DeliveryChannel``.

Design
------
The Streamlit app does not run a long-lived event loop, so we use an
external scheduler. This module is the worker; cron (or a Docker
sibling container) is the scheduler.

    # Default cron line — 07:00 UTC every day
    0 7 * * * cd /path/to/ship && /usr/bin/python3 -m worker.scheduler --push >> logs/scheduler.log 2>&1

The module is intentionally crash-proof: ``run_daily_briefing_job``
catches every exception and always returns a populated
``ReportJobResult``. The CLI exits 0 on success, 1 on failure.

This module must NOT import ``streamlit``. It is invoked outside the
Streamlit process and therefore has no ``st.*`` available.
"""
from __future__ import annotations

import argparse
import functools
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
#  Run-tracking decorator
# ─────────────────────────────────────────────────────────────────────────────
#
# Every ``run_*_job`` is wrapped with :func:`_track_run` so the platform
# persists a per-invocation record (start/finish, status, result, error).
# The dashboard reads those records via ``state.worker_runs`` — operators
# no longer need to grep ``logs/scheduler.log`` to answer "did the prune
# fire last night, and what did it return?".
#
# The decorator never alters the wrapped job's return value or exception
# behaviour. It records start, runs the job, records finish in a
# ``finally`` block, and on exception records ``status='error'`` then
# re-raises so the existing internal try/except in each job is the
# canonical error handler. The lazy import of ``state.worker_runs``
# means a broken state module can never take the worker down.


def _track_run(fn: Callable) -> Callable:
    """Decorator that persists one ``WorkerJobRun`` per invocation.

    Wrap a worker-job function so its execution is recorded to
    ``state.worker_runs``. The decorator is intentionally minimal:

      * No changes to ``fn``'s signature, return value, or exception
        behaviour. The wrapped function looks identical to the caller.
      * Recording failures (DB locked, kv_state row corrupt, etc.) are
        swallowed — observability MUST NEVER block the worker.
      * The recorded ``result`` is the function's return value; the
        record_run helper coerces non-JSON-serializable values so even
        ``list[HealthPing]`` (from ``run_health_ping_job``) persists as
        a useful ``_repr`` summary.

    Each ``run_*_job`` already has an internal try/except that swallows
    exceptions and returns a safe default. This decorator's exception
    handler is therefore largely belt-and-braces — but it stays in place
    so that, if a future job is added that DOES raise, the error is still
    captured in the dashboard.
    """
    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        started_at = datetime.now(timezone.utc).isoformat()
        result: Any = None
        error_message: Optional[str] = None
        status = "ok"
        try:
            result = fn(*args, **kwargs)
            return result
        except Exception as exc:
            status = "error"
            error_message = str(exc)
            raise
        finally:
            finished_at = datetime.now(timezone.utc).isoformat()
            try:
                # Lazy import so a broken state module can NEVER take the
                # worker down. The whole record_run call is wrapped in a
                # second try just for extra paranoia — record_run already
                # never raises, but the import itself can fail in weird
                # environments.
                from state.worker_runs import record_run
                record_run(
                    fn.__name__,
                    started_at=started_at,
                    finished_at=finished_at,
                    status=status,
                    result=result,
                    error_message=error_message,
                )
            except Exception as record_exc:
                logger.debug(
                    f"_track_run: record_run failed for {fn.__name__}: "
                    f"{record_exc}"
                )

    return _wrapped


# ─────────────────────────────────────────────────────────────────────────────
#  Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ReportJobResult:
    """Outcome of a single ``run_daily_briefing_job`` invocation.

    Attributes
    ----------
    report_id:
        UUID of the persisted report. Empty string when the job failed
        before reaching the save step.
    file_path:
        Absolute path to the persisted HTML file on disk. Empty when
        the job failed before saving.
    success:
        True iff the report was built AND saved without raising.
    duration_s:
        Wall-clock duration of the job in seconds, including build,
        render, save, and optional channel delivery.
    error_msg:
        Empty when success is True. Otherwise a short, human-readable
        string describing what went wrong (caught exception or a save
        failure).
    """

    report_id: str = ""
    file_path: str = ""
    success: bool = False
    duration_s: float = 0.0
    error_msg: str = ""


# ─────────────────────────────────────────────────────────────────────────────
#  Data loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    """Load the project config.yaml without any Streamlit decorators."""
    try:
        import yaml
        cfg_path = Path(__file__).parent.parent / "config.yaml"
        with open(cfg_path) as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning(f"_load_config failed: {exc}")
        return {}


def load_data_bundle() -> dict:
    """Load the freight/macro/stock/port/route/insight bundle the report needs.

    This mirrors the data-loading block in ``app.py`` but never touches
    Streamlit — no ``st.cache_data`` wrappers, no ``st.spinner``. Each
    loader is wrapped so one missing data source degrades to an empty
    dict/list instead of crashing the worker. The returned bundle has
    the same keys ``build_investor_report`` expects.

    Returns
    -------
    dict
        Keys: ``port_results``, ``route_results``, ``insights``,
        ``freight_data``, ``macro_data``, ``stock_data``, ``news_items``,
        ``source`` (string identifying how the bundle was loaded — used
        by the CLI for diagnostic output).
    """
    bundle: dict = {
        "port_results": [],
        "route_results": [],
        "insights": [],
        "freight_data": {},
        "macro_data": {},
        "stock_data": {},
        "news_items": [],
        "source": "live",
    }

    cfg = _load_config()
    lookback = 90  # matches the app default

    # Stock data
    try:
        from data.cache_manager import CacheManager
        from data.stock_feed import fetch_all_stocks

        tickers = cfg.get("shipping_stocks", []) + cfg.get("sector_etfs", [])
        cache = CacheManager()
        bundle["stock_data"] = fetch_all_stocks(
            tickers,
            lookback,
            cache,
            ttl_hours=cfg.get("cache", {}).get("stocks_ttl_hours", 24),
        )
    except Exception as exc:
        logger.warning(f"load_data_bundle: stock fetch failed: {exc}")

    # Macro (FRED)
    try:
        from data.cache_manager import CacheManager
        from data.fred_feed import fetch_macro_series

        cache = CacheManager()
        bundle["macro_data"] = fetch_macro_series(
            lookback + 90,
            cache,
            ttl_hours=cfg.get("cache", {}).get("fred_ttl_hours", 24),
        )
    except Exception as exc:
        logger.warning(f"load_data_bundle: macro fetch failed: {exc}")

    # Freight rates
    try:
        from data.cache_manager import CacheManager
        from data.freight_scraper import fetch_fbx_rates

        cache = CacheManager()
        bundle["freight_data"] = fetch_fbx_rates(
            lookback + 30,
            cache,
            ttl_hours=cfg.get("cache", {}).get("freight_ttl_hours", 24),
        )
    except Exception as exc:
        logger.warning(f"load_data_bundle: freight fetch failed: {exc}")

    # Trade / AIS / World Bank — needed for port_results
    trade_data: dict = {}
    ais_data: dict = {}
    wb_data: dict = {}
    try:
        from data.cache_manager import CacheManager
        from data.comtrade_feed import fetch_all_ports

        cache = CacheManager()
        trade_data = fetch_all_ports(
            3, cache, ttl_hours=cfg.get("cache", {}).get("comtrade_ttl_hours", 168)
        )
    except Exception as exc:
        logger.warning(f"load_data_bundle: trade fetch failed: {exc}")

    try:
        from data.ais_feed import fetch_vessel_counts
        from data.cache_manager import CacheManager

        cache = CacheManager()
        ais_data = fetch_vessel_counts(
            cache, ttl_hours=cfg.get("cache", {}).get("ais_ttl_hours", 6)
        )
    except Exception as exc:
        logger.warning(f"load_data_bundle: ais fetch failed: {exc}")

    try:
        from data.cache_manager import CacheManager
        from data.worldbank_feed import fetch_port_throughput

        cache = CacheManager()
        wb_data = fetch_port_throughput(
            cache, ttl_hours=cfg.get("cache", {}).get("worldbank_ttl_hours", 168)
        )
    except Exception as exc:
        logger.warning(f"load_data_bundle: worldbank fetch failed: {exc}")

    # Analysis pipeline — ports → routes → insights
    try:
        from ports.demand_analyzer import analyze_all_ports
        from routes.optimizer import optimize_all_routes

        bundle["port_results"] = analyze_all_ports(trade_data, ais_data, wb_data)
        bundle["route_results"] = optimize_all_routes(
            bundle["port_results"], bundle["freight_data"], bundle["macro_data"]
        )
    except Exception as exc:
        logger.warning(f"load_data_bundle: ports/routes analysis failed: {exc}")

    try:
        from engine.scorer import InsightScorer

        bundle["insights"] = InsightScorer(cfg).score_all(
            bundle["port_results"],
            bundle["route_results"],
            bundle["macro_data"],
            bundle["stock_data"],
        )
    except Exception as exc:
        logger.warning(f"load_data_bundle: insight scoring failed: {exc}")

    return bundle


# ─────────────────────────────────────────────────────────────────────────────
#  Main job
# ─────────────────────────────────────────────────────────────────────────────

@_track_run
def run_daily_briefing_job(
    data_bundle: dict,
    *,
    push_to_channels: bool = False,
) -> ReportJobResult:
    """Build, render, persist, and (optionally) deliver one investor report.

    Always returns a populated ``ReportJobResult``. The function never
    raises — any exception is captured into ``error_msg`` and surfaced
    via ``success=False``.

    Parameters
    ----------
    data_bundle:
        Output of ``load_data_bundle``. The same six dict keys
        ``build_investor_report`` accepts as positional arguments
        (port_results, route_results, insights, freight_data,
        macro_data, stock_data, news_items).
    push_to_channels:
        When True, every enabled ``DeliveryChannel`` in the
        ``delivery_channels`` SQLite table receives a ``deliver_pending``
        call covering the last 24 hours of alerts. When False, the
        delivery step is skipped entirely.

    Returns
    -------
    ReportJobResult
        ``success`` reflects whether the build+save round-trip
        completed. Channel delivery is best-effort and does NOT flip
        ``success`` to False if a single channel fails; those failures
        are logged but the report itself is considered shipped.
    """
    start = time.monotonic()
    bundle = data_bundle or {}

    try:
        # Lazy imports keep the module light when, e.g., tests patch
        # only a subset of dependencies.
        from processing.investor_report_engine import build_investor_report
        from utils.investor_report_html import render_investor_report_html
        from utils.report_history import save_report

        logger.info("run_daily_briefing_job: building report")
        report = build_investor_report(
            port_results=bundle.get("port_results", []),
            route_results=bundle.get("route_results", []),
            insights=bundle.get("insights", []),
            freight_data=bundle.get("freight_data", {}),
            macro_data=bundle.get("macro_data", {}),
            stock_data=bundle.get("stock_data", {}),
            news_items=bundle.get("news_items"),
        )

        logger.info("run_daily_briefing_job: rendering HTML")
        html = render_investor_report_html(report)

        logger.info("run_daily_briefing_job: persisting to disk")
        meta = save_report(html, report)

        if meta is None:
            duration = time.monotonic() - start
            logger.warning("run_daily_briefing_job: save_report returned None")
            return ReportJobResult(
                report_id="",
                file_path="",
                success=False,
                duration_s=duration,
                error_msg="save_report returned None",
            )

        # Optional outbound delivery — best-effort, never flips success
        if push_to_channels:
            try:
                from engine.alert_delivery import deliver_pending, load_channels

                channels = load_channels()
                since = datetime.now(timezone.utc) - timedelta(hours=24)
                for channel in channels:
                    if not channel.enabled:
                        continue
                    try:
                        deliver_pending(channel, since=since)
                    except Exception as exc:
                        logger.warning(
                            f"run_daily_briefing_job: delivery to {channel.name!r} failed: {exc}"
                        )
            except Exception as exc:
                logger.warning(f"run_daily_briefing_job: delivery step failed: {exc}")

        duration = time.monotonic() - start
        logger.info(
            f"run_daily_briefing_job: success report_id={meta.report_id} "
            f"file={meta.file_path} duration_s={duration:.2f}"
        )
        return ReportJobResult(
            report_id=meta.report_id,
            file_path=meta.file_path,
            success=True,
            duration_s=duration,
            error_msg="",
        )
    except Exception as exc:
        duration = time.monotonic() - start
        logger.error(f"run_daily_briefing_job: unhandled exception: {exc}")
        return ReportJobResult(
            report_id="",
            file_path="",
            success=False,
            duration_s=duration,
            error_msg=str(exc),
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Telemetry retention
# ─────────────────────────────────────────────────────────────────────────────

@_track_run
def run_telemetry_prune_job(retention_days: int = 90) -> int:
    """Prune ``llm_calls`` rows older than ``retention_days`` days.

    Thin wrapper around ``engine.llm_telemetry.prune_old_calls`` that
    adds logging and shields the caller from any exception. Designed to
    be invoked once per day from ``main`` AFTER ``run_daily_briefing_job``
    so the daily cron does both: build the report AND prune old
    telemetry.

    Returns the number of rows deleted (``0`` on no-op or any error).
    Never raises — a prune failure must never block the briefing job.
    """
    try:
        from engine.llm_telemetry import prune_old_calls

        deleted = prune_old_calls(retention_days=retention_days)
        logger.info(
            f"run_telemetry_prune_job: deleted={deleted} rows "
            f"(retention_days={retention_days})"
        )
        return int(deleted)
    except Exception as exc:
        logger.warning(f"run_telemetry_prune_job: failed: {exc}")
        return 0


@_track_run
def run_perf_prune_job(retention_days: int = 30) -> int:
    """Prune ``tab_render_events`` rows older than ``retention_days`` days.

    Thin wrapper around ``engine.perf_telemetry.prune_old_events`` —
    mirrors ``run_telemetry_prune_job`` for the LLM-call table, but
    against the per-tab render-duration telemetry instead. Designed to
    be invoked once per day from ``main`` AFTER both the briefing job
    and the LLM-call prune so the daily cron handles all retention in
    a single pass.

    Default retention is 30 days — render events accumulate faster than
    LLM calls and the operational question they answer ("which tabs are
    slow right now?") only needs the recent picture; a 90-day window
    would be wasteful.

    Returns the number of rows deleted (``0`` on no-op or any error).
    Never raises — a prune failure must never block the briefing job.
    """
    try:
        from engine.perf_telemetry import prune_old_events

        deleted = prune_old_events(retention_days=retention_days)
        logger.info(
            f"run_perf_prune_job: deleted={deleted} rows "
            f"(retention_days={retention_days})"
        )
        return int(deleted)
    except Exception as exc:
        logger.warning(f"run_perf_prune_job: failed: {exc}")
        return 0


@_track_run
def run_health_ping_job() -> list:
    """Run every data-source health probe and record the outcomes.

    Thin wrapper around ``engine.source_health.ping_all_sources`` that
    adds logging and shields the caller from any exception. Designed to
    be invoked once per daily worker pass — each probe writes one row
    to ``data_source_health`` so the platform can answer "is FRED
    degrading right now?" without scrolling through logs.

    Returns the list of ``HealthPing`` results (empty list on any
    error). Never raises — a probe failure must never block the
    briefing job or its sibling prune jobs.
    """
    try:
        from engine.source_health import ping_all_sources

        pings = ping_all_sources()
        # Roll-up log: one line per source so the worker log stays
        # readable even when several feeds are degrading at once.
        for p in pings:
            logger.info(
                f"run_health_ping_job: source={p.source} "
                f"status={p.status} duration_ms={p.duration_ms}"
                + (f" error={p.error_msg!r}" if p.status != "up" else "")
            )
        return list(pings)
    except Exception as exc:
        logger.warning(f"run_health_ping_job: failed: {exc}")
        return []


@_track_run
def run_health_prune_job(retention_days: int = 30) -> int:
    """Prune ``data_source_health`` rows older than ``retention_days``.

    Thin wrapper around ``engine.source_health.prune_old_pings`` —
    mirrors ``run_telemetry_prune_job`` / ``run_perf_prune_job`` for the
    health-ping table. Designed to be invoked once per day from
    ``main`` AFTER ``run_health_ping_job`` so the new ping row lives
    inside the retention window even at the boundary.

    Default retention is 30 days — health pings accumulate at ~one
    row per source per worker tick, well under the LLM-call default.

    Returns the number of rows deleted (``0`` on no-op or any error).
    Never raises — a prune failure must never block the briefing job.
    """
    try:
        from engine.source_health import prune_old_pings

        deleted = prune_old_pings(retention_days=retention_days)
        logger.info(
            f"run_health_prune_job: deleted={deleted} rows "
            f"(retention_days={retention_days})"
        )
        return int(deleted)
    except Exception as exc:
        logger.warning(f"run_health_prune_job: failed: {exc}")
        return 0


@_track_run
def run_source_health_alert_job() -> dict:
    """Check the current source-health snapshot and auto-fire alerts for
    degraded feeds.

    Thin wrapper around ``engine.source_health_alerts.check_source_health_and_fire``
    that adds logging and shields the caller from any exception. Designed
    to run frequently (every 5 minutes from the cron job loop) so a feed
    going red is surfaced as an alert within minutes rather than waiting
    for the daily worker pass.

    The orchestrator already swallows per-source failures internally —
    this wrapper is belt-and-braces: even if the orchestrator itself
    raises, the worker continues. Returns the count dict
    ``{"fired": N, "skipped_cooldown": N, "errored": N}`` (or all zeros
    on a top-level exception).

    Never raises — a check failure must never block any sibling job.
    """
    try:
        from engine.source_health_alerts import check_source_health_and_fire

        counts = check_source_health_and_fire()
        logger.info(
            f"run_source_health_alert_job: fired={counts.get('fired', 0)} "
            f"skipped_cooldown={counts.get('skipped_cooldown', 0)} "
            f"errored={counts.get('errored', 0)}"
        )
        return counts
    except Exception as exc:
        logger.warning(f"run_source_health_alert_job: failed: {exc}")
        return {"fired": 0, "skipped_cooldown": 0, "errored": 0}


@_track_run
def run_perf_budget_check_job() -> dict:
    """Check every per-tab perf budget and fire alerts for breaches.

    Thin wrapper around ``engine.perf_budgets.check_and_alert`` that
    adds logging and shields the caller from any exception. Designed
    to run once per hour from the cron loop — tab-render telemetry
    accumulates fast enough that hourly is the right cadence (slower
    than the 5-min source-health pass, but tighter than the once-a-day
    prune jobs).

    The orchestrator already swallows per-breach failures internally —
    this wrapper is belt-and-braces: even if the orchestrator itself
    raises, the worker continues. Returns the count dict shaped like
    ``{"checked": N, "breached": N, "alerted": N, "skipped_cooldown": N}``
    (or all zeros on a top-level exception).

    Never raises — a check failure must never block any sibling job.
    """
    try:
        from engine.perf_budgets import check_and_alert

        counts = check_and_alert()
        logger.info(
            f"run_perf_budget_check_job: checked={counts.get('checked', 0)} "
            f"breached={counts.get('breached', 0)} "
            f"alerted={counts.get('alerted', 0)} "
            f"skipped_cooldown={counts.get('skipped_cooldown', 0)}"
        )
        return counts
    except Exception as exc:
        logger.warning(f"run_perf_budget_check_job: failed: {exc}")
        return {"checked": 0, "breached": 0, "alerted": 0, "skipped_cooldown": 0}


@_track_run
def run_anomaly_detection_job() -> dict:
    """Run anomaly detection across every tracked metric + fire alerts.

    Thin wrapper around ``engine.anomaly_detect.check_and_alert_anomalies``
    that adds logging and shields the caller from any exception. The
    underlying orchestrator already swallows per-metric failures
    internally — this wrapper is belt-and-braces: even if the
    orchestrator itself raises, the worker continues.

    Cadence is "sub-daily but not aggressive" — every 6 hours when the
    cron loop fires that often. Daily passes miss fast drift; hourly
    is overkill for a check whose baseline window is 30 days. The
    underlying ``_COOLDOWN_HOURS`` defaults to 24h so an already-fired
    metric stays quiet until tomorrow regardless of cadence.

    Returns the count dict shaped like the perf-budget + source-health
    alerters: ``{"checked": N, "detected": N, "alerted": N,
    "skipped_cooldown": N}`` (or all zeros on a top-level exception).

    Never raises — a check failure must never block any sibling job.
    """
    try:
        from engine.anomaly_detect import check_and_alert_anomalies

        counts = check_and_alert_anomalies()
        logger.info(
            f"run_anomaly_detection_job: checked={counts.get('checked', 0)} "
            f"detected={counts.get('detected', 0)} "
            f"alerted={counts.get('alerted', 0)} "
            f"skipped_cooldown={counts.get('skipped_cooldown', 0)}"
        )
        return counts
    except Exception as exc:
        logger.warning(f"run_anomaly_detection_job: failed: {exc}")
        return {"checked": 0, "detected": 0, "alerted": 0, "skipped_cooldown": 0}


@_track_run
def run_alert_escalation_job(now: Optional[datetime] = None) -> dict:
    """Walk every unacked alert and escalate any whose next chain step
    has come due.

    Thin wrapper around ``engine.alert_escalation.run_escalation_pass``
    that adds logging and shields the caller from any exception.
    Designed to run frequently (every 5 minutes from the cron loop)
    so an unacked CRITICAL on rule X with a 15-min step-1 timer
    actually escalates within minutes of the timer expiring, not at
    the next daily worker pass.

    The engine orchestrator already swallows per-alert failures
    internally — this wrapper is belt-and-braces: even if the engine
    itself raises, the worker continues. Returns the count dict
    ``{"checked": N, "escalated": N, "failed": N}`` (or all zeros on
    a top-level exception). Same shape, same posture, as the
    source-health-alert + perf-budget-check wrappers.

    ``now`` (optional) is forwarded straight through to the engine so
    tests can pin the clock without monkeypatching datetime.

    Never raises — an escalation failure must NEVER block any sibling
    job. The engine itself is non-raising by contract; this guard is
    defence in depth.
    """
    try:
        from engine.alert_escalation import run_escalation_pass

        counts = run_escalation_pass(now=now)
        logger.info(
            f"run_alert_escalation_job: checked={counts.get('checked', 0)} "
            f"escalated={counts.get('escalated', 0)} "
            f"failed={counts.get('failed', 0)}"
        )
        return counts
    except Exception as exc:
        logger.warning(f"run_alert_escalation_job: failed: {exc}")
        return {"checked": 0, "escalated": 0, "failed": 0}


@_track_run
def run_port_supply_snapshot_job(
    *,
    container_type: str = "40FT_DRY",
    min_diff_delta_days: float = 1.0,
) -> dict:
    """Persist today's port-supply snapshot + log the diff vs prior.

    Thin wrapper around
    ``processing.port_supply_history.run_daily_snapshot_job`` that adds
    logging + shields the caller from any exception (matches the
    contract of the other run_*_job helpers in this module).

    The underlying job:
      1. Builds today's port supply chains
      2. Writes ``port_supply_summary_<container>.csv`` under
         ``cache/port_supply_snapshots/<today>/``
      3. Finds the most recent prior snapshot for the same container
         type (default 14-day lookback so weekend gaps are tolerated)
      4. If found, diffs against it and returns the structured
         ``DiffReport`` so the count of severity shifts / entered-
         deficit transitions can be logged inline

    Returns a count dict so the caller can fold it into telemetry:
      ``{"ok": bool, "saved_bytes": int, "diff_present": bool,
         "severity_shifts": int, "entered_deficit": int,
         "exited_deficit": int, "deficit_moves": int,
         "digest_paths": dict}``

    ``digest_paths`` is populated only when the diff is material enough
    to ship (``delivery.port_supply_shock_digest.should_send`` returns
    True). On quiet days it stays empty so downstream channels know
    there's nothing new to dispatch.

    Never raises — failures land in ``ok=False`` + a logger.warning.
    Designed for the same daily cadence as the briefing job.
    """
    try:
        from processing.port_supply_history import run_daily_snapshot_job

        result = run_daily_snapshot_job(
            container_type=container_type,
            min_diff_delta_days=min_diff_delta_days,
        )
    except Exception as exc:   # pragma: no cover - defensive
        logger.warning(f"run_port_supply_snapshot_job: top-level failure: {exc}")
        return {
            "ok": False, "saved_bytes": 0, "diff_present": False,
            "severity_shifts": 0, "entered_deficit": 0,
            "exited_deficit": 0, "deficit_moves": 0,
            "digest_paths": {},
        }

    counts = {
        "ok":              bool(result.ok),
        "saved_bytes":     int(result.bytes_written),
        "diff_present":    result.diff is not None,
        "severity_shifts": 0,
        "entered_deficit": 0,
        "exited_deficit":  0,
        "deficit_moves":   0,
        "digest_paths":    {},   # populated below if the digest gets persisted
    }
    if result.diff is not None:
        counts["severity_shifts"] = len(result.diff.severity_shifts)
        counts["entered_deficit"] = len(result.diff.entered_deficit)
        counts["exited_deficit"]  = len(result.diff.exited_deficit)
        counts["deficit_moves"]   = len(result.diff.deficit_moves)

    if not result.ok:
        logger.warning(
            f"run_port_supply_snapshot_job: save failed — {result.error_msg}"
        )
        return counts

    if result.diff is None:
        logger.info(
            f"run_port_supply_snapshot_job: saved "
            f"{result.bytes_written:,}B → {result.snapshot_path} "
            f"(no prior snapshot to diff against)"
        )
    else:
        logger.info(
            f"run_port_supply_snapshot_job: saved "
            f"{result.bytes_written:,}B → {result.snapshot_path}; "
            f"diff vs {result.prior_snapshot_date}: "
            f"severity_shifts={counts['severity_shifts']} "
            f"entered_deficit={counts['entered_deficit']} "
            f"exited_deficit={counts['exited_deficit']} "
            f"deficit_moves={counts['deficit_moves']}"
        )
        if result.error_msg:
            # The save succeeded but the diff phase had a non-fatal issue
            # (rare — most often a corrupted prior CSV). Log it as a
            # warning so the worker output makes the partial state visible.
            logger.warning(
                f"run_port_supply_snapshot_job: diff warning — {result.error_msg}"
            )

    # ── Persist HTML / text / subject digest artifacts ─────────────────────
    # Defensive: the digest is downstream of the save; a render failure
    # must NEVER fail the snapshot or its diff. Quiet days (should_send
    # returns False — empty diff or None) skip persistence entirely so
    # downstream channels know there's nothing new to dispatch and the
    # snapshot dir stays tidy.
    try:
        from delivery.port_supply_shock_digest import (
            build_subject_line,
            render_html,
            render_plain_text,
            should_send,
        )

        if should_send(result.diff):
            snapshot_path = Path(result.snapshot_path)
            snapshot_dir = snapshot_path.parent
            container_slug = container_type.lower()
            html_path = snapshot_dir / f"digest_{container_slug}.html"
            text_path = snapshot_dir / f"digest_{container_slug}.txt"
            subj_path = snapshot_dir / f"digest_{container_slug}.subject.txt"

            # Compute the anomaly band against trailing snapshot history
            # so the digest can flag shock days in the subject + body.
            # Defensive — anomaly compute failure must NOT block digest
            # render; fall back to empty band (= normal-day path).
            anomaly_band, anomaly_explanation = "", ""
            try:
                from datetime import date as _date
                from processing.snapshot_diff_anomaly import (
                    build_history_from_snapshots,
                    compute_diff_magnitude,
                    score_anomaly,
                )
                today_dt = _date.fromisoformat(result.today)
                history = build_history_from_snapshots(
                    container_type=container_type,
                    today=today_dt,
                    window_days=30,
                )
                today_mag = compute_diff_magnitude(result.diff)
                today_mag.date_iso = result.today
                score = score_anomaly(today_mag, history)
                anomaly_band = score.anomaly_band
                anomaly_explanation = score.explanation
            except Exception as anomaly_exc:   # pragma: no cover - defensive
                logger.warning(
                    f"run_port_supply_snapshot_job: anomaly score failed "
                    f"({anomaly_exc}) — digest will render without band"
                )

            html_body = render_html(
                result.diff,
                container_type=container_type,
                snapshot_date_iso=result.today,
                prior_date_iso=result.prior_snapshot_date,
                anomaly_band=anomaly_band,
                anomaly_explanation=anomaly_explanation,
            )
            text_body = render_plain_text(
                result.diff,
                container_type=container_type,
                snapshot_date_iso=result.today,
                prior_date_iso=result.prior_snapshot_date,
                anomaly_band=anomaly_band,
                anomaly_explanation=anomaly_explanation,
            )
            subject = build_subject_line(
                result.diff,
                container_type=container_type,
                snapshot_date_iso=result.today,
                anomaly_band=anomaly_band,
            )

            html_path.write_text(html_body, encoding="utf-8")
            text_path.write_text(text_body, encoding="utf-8")
            subj_path.write_text(subject + "\n", encoding="utf-8")

            counts["digest_paths"] = {
                "html":    str(html_path),
                "text":    str(text_path),
                "subject": str(subj_path),
            }
            logger.info(
                f"run_port_supply_snapshot_job: digest persisted to "
                f"{snapshot_dir} ({subject!r})"
            )
    except Exception as exc:   # pragma: no cover - defensive
        # Snapshot itself already landed — a digest failure is purely
        # a delivery-side concern. Log + carry on so the diff still
        # surfaces in the counts dict + the upstream cron tick.
        logger.warning(
            f"run_port_supply_snapshot_job: digest persistence failed: {exc}"
        )

    return counts


@_track_run
def run_cargo_mix_snapshot_job(
    *,
    window_days: int = 14,
    jsd_anomaly_threshold: float = 0.15,
) -> dict:
    """Persist today's per-route cargo mix + identify anomalous routes.

    Wraps ``processing.cargo_mix_history.run_daily_cargo_mix_snapshot_job``
    with the canonical contract — never raises, returns count dict,
    logs the anomaly count inline. Once the trailing window populates
    (>= 1 prior day), CARGO_FLOW_ANOMALY alerts will fire from
    ``engine.alert_engine_v2.check_cargo_flow_anomaly_alerts`` on the
    same daily tick.

    Returns ``{"ok": bool, "n_routes_saved": int, "bytes_written": int,
    "n_anomaly_routes": int, "anomaly_routes": list[str]}``.
    """
    try:
        from processing.cargo_mix_history import (
            run_daily_cargo_mix_snapshot_job as _impl,
        )
        result = _impl(
            window_days=window_days,
            jsd_anomaly_threshold=jsd_anomaly_threshold,
        )
    except Exception as exc:   # pragma: no cover - defensive
        logger.warning(f"run_cargo_mix_snapshot_job: top-level failure: {exc}")
        return {
            "ok": False, "n_routes_saved": 0, "bytes_written": 0,
            "n_anomaly_routes": 0, "anomaly_routes": [],
        }

    counts = {
        "ok":               bool(result.ok),
        "n_routes_saved":   int(result.n_routes_saved),
        "bytes_written":    int(result.bytes_written),
        "n_anomaly_routes": int(result.n_anomaly_routes),
        "anomaly_routes":   list(result.anomaly_routes),
    }
    if result.ok:
        anomaly_clause = (
            f"; anomalous: {', '.join(result.anomaly_routes)}"
            if result.anomaly_routes else ""
        )
        logger.info(
            f"run_cargo_mix_snapshot_job: saved "
            f"{result.bytes_written:,}B for {result.n_routes_saved} route(s)"
            f"{anomaly_clause}"
        )
    else:
        logger.warning(
            f"run_cargo_mix_snapshot_job: save failed — {result.error_msg}"
        )
    return counts


@_track_run
def run_company_risk_snapshot_job(
    *,
    container_type: str = "40FT_DRY",
    max_lookback_days: int = 14,
) -> dict:
    """Persist today's per-ticker risk scores + detect band transitions.

    Wraps ``processing.company_risk_history.run_daily_company_risk_snapshot_job``
    with the canonical contract. Logs band transitions inline so
    operators see "ZIM Elevated → High" the same tick it appears.

    Returns ``{"ok": bool, "n_tickers_saved": int, "bytes_written": int,
    "n_band_transitions": int, "band_transitions": list[dict]}``.
    """
    try:
        from processing.company_risk_history import (
            run_daily_company_risk_snapshot_job as _impl,
        )
        result = _impl(
            container_type=container_type,
            max_lookback_days=max_lookback_days,
        )
    except Exception as exc:   # pragma: no cover - defensive
        logger.warning(f"run_company_risk_snapshot_job: top-level failure: {exc}")
        return {
            "ok": False, "n_tickers_saved": 0, "bytes_written": 0,
            "n_band_transitions": 0, "band_transitions": [],
        }

    counts = {
        "ok":                   bool(result.ok),
        "n_tickers_saved":      int(result.n_tickers_saved),
        "bytes_written":        int(result.bytes_written),
        "n_band_transitions":   len(result.band_transitions),
        "band_transitions":     list(result.band_transitions),
    }
    if result.ok:
        transition_clause = ""
        if result.band_transitions:
            sample = "; ".join(
                f"{t['ticker']} {t['prior_band']}→{t['current_band']}"
                for t in result.band_transitions[:3]
            )
            transition_clause = f"; band changes: {sample}"
        logger.info(
            f"run_company_risk_snapshot_job: saved "
            f"{result.bytes_written:,}B for {result.n_tickers_saved} ticker(s)"
            f"{transition_clause}"
        )
    else:
        logger.warning(
            f"run_company_risk_snapshot_job: save failed — {result.error_msg}"
        )
    return counts


@_track_run
def run_snapshot_integrity_check_job(
    *,
    container_type: str = "40FT_DRY",
    since_days: int = 14,
) -> dict:
    """Verify recent snapshot files are present + parseable.

    Wraps ``processing.snapshot_integrity.check_all_snapshots`` with the
    standard contract — never raises, returns count dict, logs the
    summary. Defaults to the last 14 days of snapshots (matches the
    diff lookback window so corruption can't go unnoticed beyond what
    the diff helper would tolerate).

    Returns ``{"ok": bool, "n_checked": int, "n_unhealthy": int,
    "n_missing": int, "n_corrupted": int,
    "oldest_problem_date": str}``. ``ok`` is True iff every checked
    snapshot is healthy.
    """
    try:
        from datetime import date as _date, timedelta as _td
        from processing.snapshot_integrity import (
            check_all_snapshots, summarize_integrity_run,
        )

        today = _date.today()
        since = today - _td(days=max(1, int(since_days)))
        reports = check_all_snapshots(
            container_type=container_type, since=since,
        )
        s = summarize_integrity_run(reports)
    except Exception as exc:   # pragma: no cover - defensive
        logger.warning(f"run_snapshot_integrity_check_job: top-level failure: {exc}")
        return {
            "ok": False, "n_checked": 0, "n_unhealthy": 0,
            "n_missing": 0, "n_corrupted": 0, "oldest_problem_date": "",
        }

    counts = {
        "ok":                   (s["n_dates_checked"] == s["n_ok"]),
        "n_checked":            int(s["n_dates_checked"]),
        "n_unhealthy":          int(s["n_dates_checked"] - s["n_ok"]),
        "n_missing":            int(s["n_missing"]),
        "n_corrupted":          int(s["n_corrupted"]),
        "oldest_problem_date":  s["oldest_problem_date"] or "",
    }
    if counts["ok"]:
        logger.info(
            f"run_snapshot_integrity_check_job: "
            f"{counts['n_checked']}/{counts['n_checked']} healthy"
        )
    else:
        logger.warning(
            f"run_snapshot_integrity_check_job: "
            f"{counts['n_unhealthy']}/{counts['n_checked']} unhealthy "
            f"(missing={counts['n_missing']} corrupted={counts['n_corrupted']} "
            f"oldest={counts['oldest_problem_date']})"
        )
    return counts


@_track_run
def run_port_supply_snapshot_gc_job(
    *,
    keep_days: int = 90,
    keep_first_of_month: bool = True,
    keep_first_of_year: bool = True,
) -> dict:
    """Prune old port-supply snapshot dirs per the retention policy.

    Thin wrapper around ``processing.port_supply_history.gc_old_snapshots``
    that adds logging + the no-raise contract. Runs AFTER the daily
    snapshot save so today's write is never accidentally garbage-
    collected by the same tick.

    Returns ``{"ok": bool, "n_dirs_scanned": int, "n_dirs_deleted": int,
    "n_bytes_freed": int, "preserved_anchors": int}``. Never raises.
    """
    try:
        from processing.port_supply_history import (
            RetentionPolicy, gc_old_snapshots,
        )

        policy = RetentionPolicy(
            keep_days=keep_days,
            keep_first_of_month=keep_first_of_month,
            keep_first_of_year=keep_first_of_year,
        )
        out = gc_old_snapshots(policy=policy)
    except Exception as exc:   # pragma: no cover - defensive
        logger.warning(f"run_port_supply_snapshot_gc_job: top-level failure: {exc}")
        return {
            "ok": False, "n_dirs_scanned": 0, "n_dirs_deleted": 0,
            "n_bytes_freed": 0, "preserved_anchors": 0,
        }

    counts = {
        "ok":                 True,
        "n_dirs_scanned":     int(out.get("n_dirs_scanned", 0)),
        "n_dirs_deleted":     int(out.get("n_dirs_deleted", 0)),
        "n_bytes_freed":      int(out.get("n_bytes_freed", 0)),
        "preserved_anchors":  len(out.get("preserved_anchors", []) or []),
    }
    logger.info(
        f"run_port_supply_snapshot_gc_job: scanned={counts['n_dirs_scanned']} "
        f"deleted={counts['n_dirs_deleted']} "
        f"bytes_freed={counts['n_bytes_freed']:,} "
        f"preserved={counts['preserved_anchors']}"
    )
    return counts


@_track_run
def run_multi_container_snapshot_job(
    *,
    container_types: Optional[list] = None,
    min_diff_delta_days: float = 1.0,
) -> dict:
    """Fan the daily port-supply snapshot out across container types.

    Thin wrapper around
    ``processing.multi_container_snapshot.run_multi_container_snapshot_job``.
    Per-container failures are isolated — one container failing doesn't
    kill the others. Returns count dict ``{"ok": bool,
    "total_bytes_written": int, "n_containers": int, "n_failed": int}``.
    Never raises.
    """
    try:
        from processing.multi_container_snapshot import (
            run_multi_container_snapshot_job as _impl,
        )

        result = _impl(
            container_types=container_types,
            min_diff_delta_days=min_diff_delta_days,
        )
    except Exception as exc:   # pragma: no cover - defensive
        logger.warning(f"run_multi_container_snapshot_job: top-level failure: {exc}")
        return {
            "ok": False, "total_bytes_written": 0,
            "n_containers": 0, "n_failed": 0,
        }

    counts = {
        "ok":                  not bool(getattr(result, "any_failed", False)),
        "total_bytes_written": int(getattr(result, "total_bytes_written", 0)),
        "n_containers":        len(getattr(result, "per_container_results", {}) or {}),
        "n_failed":            sum(
            1 for r in (result.per_container_results or {}).values()
            if not getattr(r, "ok", False)
        ),
    }
    logger.info(
        f"run_multi_container_snapshot_job: containers={counts['n_containers']} "
        f"failed={counts['n_failed']} "
        f"total_bytes={counts['total_bytes_written']:,}"
    )
    return counts


def run_delivery_retry_job(now: Optional[datetime] = None) -> dict:
    """Walk every due pending delivery retry and re-dispatch.

    Thin wrapper around ``engine.delivery_retry.run_retry_pass`` that
    adds logging and shields the caller from any exception. Designed
    to run frequently (every 5 minutes from the cron loop) so a
    transient transport failure (HTTP 5xx, network timeout, SMTP
    blip) is re-attempted within minutes — fast enough that a CRITICAL
    alert isn't stuck in the queue while operators are on shift.

    The engine orchestrator already swallows per-row failures
    internally — this wrapper is belt-and-braces: even if the engine
    itself raises, the worker continues. Returns the count dict
    ``{"processed": N, "succeeded": N, "failed": N,
    "max_retries_exhausted": N}`` (or all zeros on a top-level
    exception). Same shape, same posture, as the alert-escalation +
    perf-budget-check wrappers.

    ``now`` (optional) is forwarded straight through to the engine so
    tests can pin the clock without monkeypatching datetime.

    Never raises — a retry-queue failure must NEVER block any sibling
    job. The engine itself is non-raising by contract; this guard is
    defence in depth.
    """
    try:
        from engine.delivery_retry import run_retry_pass

        counts = run_retry_pass(now=now)
        logger.info(
            f"run_delivery_retry_job: "
            f"processed={counts.get('processed', 0)} "
            f"succeeded={counts.get('succeeded', 0)} "
            f"failed={counts.get('failed', 0)} "
            f"max_retries_exhausted={counts.get('max_retries_exhausted', 0)}"
        )
        return counts
    except Exception as exc:
        logger.warning(f"run_delivery_retry_job: failed: {exc}")
        return {
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "max_retries_exhausted": 0,
        }


@_track_run
def run_bulk_export_prune_job(keep_n: int = 5) -> int:
    """Prune ``cache/exports/*.tar.gz`` down to the newest ``keep_n``.

    Thin wrapper around ``utils.bulk_export.prune_old_exports`` that
    adds logging and shields the caller from any exception. Bulk exports
    are larger than telemetry rows (megabytes per archive vs. bytes per
    row) so even a small backlog adds up on disk — keeping the newest 5
    is enough for "I broke something yesterday, roll me back" without
    growing without bound.

    Designed to be invoked once per day from ``main`` AFTER the
    health prune so a freshly-created export from the same daily run is
    still inside the retention window.

    Returns the number of archives deleted (``0`` on no-op or any error).
    Never raises — a prune failure must never block the briefing job.
    """
    try:
        from utils.bulk_export import prune_old_exports

        deleted = prune_old_exports(keep_n=keep_n)
        logger.info(
            f"run_bulk_export_prune_job: deleted={deleted} archives "
            f"(keep_n={keep_n})"
        )
        return int(deleted)
    except Exception as exc:
        logger.warning(f"run_bulk_export_prune_job: failed: {exc}")
        return 0


@_track_run
def run_silence_cleanup_job(retention_days: int = 30) -> int:
    """Sweep expired alert silences from the ``alert_silences`` table.

    Thin wrapper around ``engine.alert_silences.cleanup_expired_silences``
    that adds logging and shields the caller from any exception. Expired
    silences are kept around for the retention window so the operator
    can audit "what was muted yesterday?"; this job sweeps anything
    older than the cutoff. Designed to run once per day from ``main``.

    Default retention is 30 days — matches the data-source health
    prune window so a silence created in response to a degraded feed
    is still queryable for the same duration as the health row that
    motivated it.

    Returns the number of rows deleted (``0`` on no-op or any error).
    Never raises — a cleanup failure must never block the briefing
    job or any sibling prune.
    """
    try:
        from engine.alert_silences import cleanup_expired_silences

        deleted = cleanup_expired_silences(retention_days=retention_days)
        logger.info(
            f"run_silence_cleanup_job: deleted={deleted} rows "
            f"(retention_days={retention_days})"
        )
        return int(deleted)
    except Exception as exc:
        logger.warning(f"run_silence_cleanup_job: failed: {exc}")
        return 0


@_track_run
def run_audit_prune_job(retention_days: int = 365) -> int:
    """Prune audit_events older than ``retention_days`` from SQLite.

    Thin wrapper around ``auth.audit.prune_old_audit_events`` that
    adds logging and shields the caller. Default 365 days — audit is
    explicitly long-lived for forensic / compliance review, but it
    can't grow forever.

    Returns the count deleted (``0`` on no-op or any error). Never raises.
    """
    try:
        from auth.audit import prune_old_audit_events

        deleted = prune_old_audit_events(retention_days=retention_days)
        logger.info(
            f"run_audit_prune_job: deleted={deleted} audit events "
            f"(retention_days={retention_days})"
        )
        return int(deleted)
    except Exception as exc:
        logger.warning(f"run_audit_prune_job: failed: {exc}")
        return 0


@_track_run
def run_report_prune_job(keep_n: int = 30) -> int:
    """Prune report_history down to ``keep_n`` newest rows.

    Thin wrapper around ``utils.report_history.prune_old_reports`` that
    adds logging and shields the caller. The auto-prune inside save_report
    already does this on every write — this job is the belt-and-suspenders
    pass that catches any drift (e.g. someone changed MAX_REPORTS mid-day).

    Returns the count deleted (``0`` on no-op or any error). Never raises.
    """
    try:
        from utils.report_history import prune_old_reports

        deleted = prune_old_reports(keep_n=keep_n)
        logger.info(
            f"run_report_prune_job: deleted={deleted} reports "
            f"(keep_n={keep_n})"
        )
        return int(deleted)
    except Exception as exc:
        logger.warning(f"run_report_prune_job: failed: {exc}")
        return 0


@_track_run
def run_alert_prune_job(retention_days: int = 180) -> int:
    """Prune ack'd alerts older than ``retention_days`` from SQLite.

    Thin wrapper around ``engine.alert_engine_v2.prune_old_alerts``
    that adds logging and shields the caller. Defaults to 180 days
    (~6 months) — long enough to backtest months of alert effectiveness,
    short enough that the table doesn't grow without bound.

    Unacknowledged alerts are NEVER auto-pruned — a 10-month-old unack'd
    CRITICAL should still be visible. Pass only_acknowledged=False from
    the CLI/admin path for a hard cleanup.

    Returns the count deleted (``0`` on no-op or any error). Never raises.
    """
    try:
        from engine.alert_engine_v2 import prune_old_alerts

        deleted = prune_old_alerts(retention_days=retention_days,
                                   only_acknowledged=True)
        logger.info(
            f"run_alert_prune_job: deleted={deleted} acknowledged alerts "
            f"(retention_days={retention_days})"
        )
        return int(deleted)
    except Exception as exc:
        logger.warning(f"run_alert_prune_job: failed: {exc}")
        return 0


@_track_run
def run_report_scheduler_job(now: Optional[datetime] = None) -> dict:
    """Fire every ``report_schedules`` row whose ``next_run_at`` is past.

    Iterates over :func:`engine.report_scheduler.get_due_schedules` and
    for each due schedule:

      1. Builds a fresh data bundle via :func:`load_data_bundle` (lazy
         — only paid once per worker tick if anything is due, not per
         schedule, but the bundle is shared across all due schedules
         on this tick).
      2. Calls :func:`run_daily_briefing_job` to build + persist the
         report. That helper already catches its own exceptions and
         returns ``ReportJobResult`` with ``success=False`` on a
         generator crash.
      3. Updates the schedule's bookkeeping columns via
         :func:`engine.report_scheduler.update_run_state`:
           - On success: ``last_run_status='ok'``, an empty
             ``last_run_message``, and ``next_run_at`` recomputed from
             the cron expression so the schedule does not re-fire.
           - On failure: ``last_run_status='error'``,
             ``last_run_message=error_msg`` (truncated to 500 chars),
             and ``next_run_at`` STILL recomputed so a broken schedule
             doesn't get stuck and keep tripping ``get_due_schedules``.

    NEVER raises. Returns a small summary dict ``{'fired': N,
    'succeeded': N, 'failed': N}`` so the main worker loop can log a
    one-liner. The empty-due-list case returns
    ``{'fired': 0, 'succeeded': 0, 'failed': 0}`` without ever loading
    the data bundle (cheap no-op when nothing is due).

    Lazy imports of the data-bundle loader and report-generator keep
    this helper light when tests want to exercise only the schedule
    plumbing.
    """
    summary = {"fired": 0, "succeeded": 0, "failed": 0}
    try:
        from engine.report_scheduler import (
            compute_next_run_at,
            get_due_schedules,
            update_run_state,
        )

        due = get_due_schedules(now=now)
        if not due:
            return summary

        # Build the data bundle once per tick — it's an expensive call
        # (fetches FRED + yfinance + freight rates, runs port +
        # route analysis) and several schedules firing on the same tick
        # would otherwise pay that cost N times.
        try:
            bundle = load_data_bundle()
        except Exception as exc:
            logger.warning(f"run_report_scheduler_job: load_data_bundle failed: {exc}")
            bundle = {}

        for sched in due:
            summary["fired"] += 1
            try:
                result = run_daily_briefing_job(bundle, push_to_channels=False)
            except Exception as exc:
                # run_daily_briefing_job already catches its own
                # exceptions, but the import / call site here gets
                # belt-and-braces guarded so one bad schedule cannot
                # take down the rest of the loop.
                logger.warning(
                    f"run_report_scheduler_job: briefing job raised for "
                    f"schedule {sched.schedule_id}: {exc}"
                )
                result = ReportJobResult(success=False, error_msg=str(exc))

            # Recompute next_run_at REGARDLESS of success — a broken
            # schedule with last_run_status='error' must still advance
            # so it doesn't keep tripping get_due_schedules every tick.
            try:
                next_dt = compute_next_run_at(sched.cron_expr)
                next_iso = next_dt.isoformat()
            except Exception as exc:
                # If the cron itself is bad (somehow saved through a
                # bypass), fall back to "one hour from now" so the
                # schedule doesn't loop. This is a last-resort guard
                # — save_schedule already validates the expression.
                logger.warning(
                    f"run_report_scheduler_job: compute_next_run_at failed for "
                    f"schedule {sched.schedule_id} cron={sched.cron_expr!r}: {exc}"
                )
                fallback = datetime.now(timezone.utc) + timedelta(hours=1)
                next_iso = fallback.isoformat()

            if result.success:
                summary["succeeded"] += 1
                update_run_state(
                    sched.schedule_id,
                    status="ok",
                    message="",
                    next_run_at=next_iso,
                )
                logger.info(
                    f"run_report_scheduler_job: schedule={sched.schedule_id} "
                    f"user={sched.user_id!r} name={sched.name!r} "
                    f"report_id={result.report_id} next_run_at={next_iso}"
                )
            else:
                summary["failed"] += 1
                # Truncate the error message so a multi-MB traceback
                # doesn't bloat the row.
                msg = (result.error_msg or "unknown error")[:500]
                update_run_state(
                    sched.schedule_id,
                    status="error",
                    message=msg,
                    next_run_at=next_iso,
                )
                logger.warning(
                    f"run_report_scheduler_job: schedule={sched.schedule_id} "
                    f"user={sched.user_id!r} name={sched.name!r} "
                    f"failed: {msg!r} next_run_at={next_iso}"
                )
        return summary
    except Exception as exc:
        logger.warning(f"run_report_scheduler_job: failed: {exc}")
        return summary


@_track_run
def run_operator_digest_job() -> list:
    """Dispatch the daily Operator Dashboard digest to every ``ops-*`` channel.

    Loads every persisted ``DeliveryChannel`` whose ``name`` starts with
    ``"ops-"`` (the convention defined in
    ``engine.operator_digest.OPERATOR_CHANNEL_PREFIX``) and calls
    ``send_operator_digest`` for each one. Returns the list of
    ``DeliveryResult`` outcomes — one per channel attempted.

    Best-effort: a failure to load channels OR a per-channel dispatch
    failure is logged but never raised. The daily cron runs this AFTER
    the alert-delivery jobs so the digest reflects the state immediately
    after any new alerts have been pushed.

    The ``ops-`` prefix convention is the channel-naming contract that
    distinguishes digest subscribers from immediate-alert channels —
    operators tag a channel as ``ops-trading-desk`` to subscribe it to
    the daily digest, leaving non-prefixed channels for per-alert
    delivery only.
    """
    try:
        from engine.alert_delivery import load_channels
        from engine.operator_digest import (
            OPERATOR_CHANNEL_PREFIX,
            send_operator_digest,
        )

        channels = load_channels()
    except Exception as exc:
        logger.warning(f"run_operator_digest_job: channel load failed: {exc}")
        return []

    results: list = []
    for channel in channels:
        # The ``ops-`` prefix is the subscriber convention. A channel
        # that doesn't carry the prefix is for immediate-alert delivery
        # only and must NOT receive the daily digest.
        if not channel.name or not channel.name.startswith(OPERATOR_CHANNEL_PREFIX):
            continue
        if not channel.enabled:
            continue
        try:
            result = send_operator_digest(channel)
            results.append(result)
            logger.info(
                f"run_operator_digest_job: channel={channel.name!r} "
                f"kind={channel.kind} success={result.success}"
                + (f" error={result.error_msg!r}" if not result.success else "")
            )
        except Exception as exc:
            logger.warning(
                f"run_operator_digest_job: send to {channel.name!r} failed: {exc}"
            )
    return results


@_track_run
def run_weekly_digest_job_wrapper(now: Optional[datetime] = None) -> dict:
    """Hourly self-gating weekly-digest dispatcher.

    Thin wrapper around ``engine.weekly_digest.run_weekly_digest_job``
    that adds logging + the ``_track_run`` bookkeeping. The engine
    helper itself self-gates on the per-user day-of-week + hour-of-day
    config AND a kv_state lock so a back-to-back hourly fire never
    double-sends to the same user. The wrapper invokes it every tick.

    Returns the engine helper's count dict
    ``{"checked": N, "fired": N, "skipped": N, "failed": N}`` (or all
    zeros on a top-level exception). Never raises.
    """
    try:
        from engine.weekly_digest import run_weekly_digest_job

        counts = run_weekly_digest_job(now=now)
        logger.info(
            f"run_weekly_digest_job_wrapper: checked={counts.get('checked', 0)} "
            f"fired={counts.get('fired', 0)} "
            f"skipped={counts.get('skipped', 0)} "
            f"failed={counts.get('failed', 0)}"
        )
        return counts
    except Exception as exc:
        logger.warning(f"run_weekly_digest_job_wrapper: failed: {exc}")
        return {"checked": 0, "fired": 0, "skipped": 0, "failed": 0}


@_track_run
def run_snapshot_prune_job(keep_n: int = 30) -> int:
    """Prune ``investor_report_snapshots`` down to the newest ``keep_n``.

    Thin wrapper around ``processing.report_snapshot.prune_old_snapshots``
    that adds logging and shields the caller from any exception. The
    briefing-tab diff only ever reads the two most-recent rows, so the
    long-tail history is purely for ad-hoc post-mortems — 30 rows is
    plenty and never weighs more than a few tens of KB on disk.

    Designed to be invoked once per day from ``main`` AFTER both the
    LLM-call prune and the render-event prune so the daily cron handles
    every snapshot/telemetry retention in a single pass.

    Returns the number of rows deleted (``0`` on no-op or any error).
    Never raises — a prune failure must never block the briefing job.
    """
    try:
        from processing.report_snapshot import prune_old_snapshots

        deleted = prune_old_snapshots(keep_n=keep_n)
        logger.info(
            f"run_snapshot_prune_job: deleted={deleted} rows "
            f"(keep_n={keep_n})"
        )
        return int(deleted)
    except Exception as exc:
        logger.warning(f"run_snapshot_prune_job: failed: {exc}")
        return 0


# ─────────────────────────────────────────────────────────────────────────────
#  CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="worker.scheduler",
        description="Build the daily investor briefing PDF/HTML and persist it.",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Also push pending alerts to every enabled DeliveryChannel.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    """CLI entry point. Returns the process exit code (0 success, 1 failure)."""
    args = _parse_args(argv)
    bundle = load_data_bundle()
    result = run_daily_briefing_job(bundle, push_to_channels=args.push)
    print(json.dumps(asdict(result), indent=2, default=str))

    # Telemetry retention runs AFTER the briefing job so a prune failure
    # cannot block the report. Wrapped in try/except for the same reason
    # the helper itself already swallows errors — belt-and-braces.
    try:
        run_telemetry_prune_job()
    except Exception as exc:
        logger.warning(f"main: telemetry prune step failed: {exc}")

    # Render-event retention runs immediately after the LLM-call prune
    # so both telemetry tables are kept in trim by the same daily cron.
    # Same belt-and-braces guard — the helper already swallows errors.
    try:
        run_perf_prune_job()
    except Exception as exc:
        logger.warning(f"main: perf prune step failed: {exc}")

    # InvestorReport snapshot retention. The diff widget only ever
    # reads the two most-recent rows; the long-tail history is purely
    # for ad-hoc post-mortems, so 30 rows is plenty. Same belt-and-
    # braces guard — the helper already swallows errors.
    try:
        run_snapshot_prune_job()
    except Exception as exc:
        logger.warning(f"main: snapshot prune step failed: {exc}")

    # Data-source health pings. Runs AFTER every prune so the briefing
    # is not slowed down by a slow probe — at the cost of the ping row
    # potentially landing just before the prune cutoff on the NEXT
    # tick. The order is "ping → prune ping rows": the freshly-written
    # row is well inside the retention window because the prune uses
    # ``now - retention_days`` as its cutoff. Same belt-and-braces
    # guard — both helpers already swallow errors.
    try:
        run_health_ping_job()
    except Exception as exc:
        logger.warning(f"main: health ping step failed: {exc}")

    try:
        run_health_prune_job()
    except Exception as exc:
        logger.warning(f"main: health prune step failed: {exc}")

    # Source-health auto-alerting. Runs AFTER the health ping so the
    # alerter classifies the freshest snapshot. The orchestrator
    # already swallows per-source errors, the wrapper swallows the
    # top-level — same belt-and-braces guard.
    try:
        run_source_health_alert_job()
    except Exception as exc:
        logger.warning(f"main: source health alert step failed: {exc}")

    # Per-tab perf-budget check. Runs AFTER the source-health alerter
    # so the two alert paths sit next to each other in the job list.
    # The orchestrator already swallows per-tab errors, the wrapper
    # swallows the top-level — same belt-and-braces guard.
    try:
        run_perf_budget_check_job()
    except Exception as exc:
        logger.warning(f"main: perf budget check step failed: {exc}")

    # Time-series anomaly detection across BDI / FBX / SCFI / WTI etc.
    # Runs AFTER the perf-budget check so the alert-firing paths sit
    # together in the job list. Detection is sub-daily (the cron fires
    # every 6h) — the per-metric cooldown of 24h prevents duplicate
    # fires regardless of cadence. Same belt-and-braces guard as the
    # sibling alerters.
    try:
        run_anomaly_detection_job()
    except Exception as exc:
        logger.warning(f"main: anomaly detection step failed: {exc}")

    # Alert escalation pass — walks unacked alerts whose next chain
    # step has come due and dispatches to the step's channel. Same
    # 5-minute cadence as the source-health alerter (the operator-
    # facing latency budget is "an unacked CRITICAL should escalate
    # within a few minutes of its timer expiring"). The orchestrator
    # already swallows per-alert errors, the wrapper swallows the
    # top-level — same belt-and-braces guard.
    try:
        run_alert_escalation_job()
    except Exception as exc:
        logger.warning(f"main: alert escalation step failed: {exc}")

    # Delivery retry pass — re-dispatches every due pending row in
    # ``delivery_retry_queue`` (v26). Designed for the same 5-minute
    # cadence so a transient transport blip (HTTP 5xx, network
    # timeout, SMTP failure) is re-attempted within minutes. The
    # orchestrator already swallows per-row errors, the wrapper
    # swallows the top-level — same belt-and-braces guard.
    try:
        run_delivery_retry_job()
    except Exception as exc:
        logger.warning(f"main: delivery retry step failed: {exc}")

    # Bulk-export archive retention. Runs AFTER the health prune so a
    # bulk-export taken on the same tick (if a future cron triggers one)
    # would still be inside the retention window. Same belt-and-braces
    # guard — the helper already swallows errors.
    try:
        run_bulk_export_prune_job()
    except Exception as exc:
        logger.warning(f"main: bulk export prune step failed: {exc}")

    # Stale acknowledged alerts. Default keeps 180 days so multi-month
    # backtests over alert_backtest still find the rows. Unacknowledged
    # alerts are never auto-pruned regardless of age. Same belt-and-
    # braces guard.
    try:
        run_alert_prune_job()
    except Exception as exc:
        logger.warning(f"main: alert prune step failed: {exc}")

    # Alert-silence cleanup — sweep expired silences past the audit
    # retention window. Runs AFTER the alert prune so a silence that
    # accompanied an alert pruned this tick stays around for the same
    # retention as any other audit-relevant row. Same belt-and-braces
    # guard — the helper already swallows errors.
    try:
        run_silence_cleanup_job()
    except Exception as exc:
        logger.warning(f"main: silence cleanup step failed: {exc}")

    # Audit retention — defaults to a full year. Audit is forensic
    # state so we keep it longer than telemetry. Same try/except guard.
    try:
        run_audit_prune_job()
    except Exception as exc:
        logger.warning(f"main: audit prune step failed: {exc}")

    # Report retention — keep the newest 30 reports on disk. save_report
    # auto-prunes on every write already; this is the belt-and-suspenders
    # pass that catches any drift.
    try:
        run_report_prune_job()
    except Exception as exc:
        logger.warning(f"main: report prune step failed: {exc}")

    # Operator Dashboard daily digest. Runs LAST so the digest reflects
    # the state after every prune / health ping has settled. Subscribers
    # are the ``DeliveryChannel`` rows whose ``name`` starts with
    # ``ops-``. Same belt-and-braces guard — the helper already swallows
    # per-channel errors and never raises.
    try:
        run_operator_digest_job()
    except Exception as exc:
        logger.warning(f"main: operator digest step failed: {exc}")

    # Weekly digest — runs every worker tick, self-gates on the
    # per-user day-of-week + hour-of-day config AND a kv_state lock so
    # a back-to-back hourly fire never double-sends to the same user.
    # Placed AFTER the operator digest so a freshly-bumped per-channel
    # budget counter is reflected in the weekly summary. Same belt-and-
    # braces guard — the helper already swallows per-user errors and
    # never raises.
    try:
        run_weekly_digest_job_wrapper()
    except Exception as exc:
        logger.warning(f"main: weekly digest step failed: {exc}")

    # Port-supply daily snapshot — writes today's per-port summary CSV
    # under cache/port_supply_snapshots/<date>/ + diffs vs the prior
    # snapshot if one exists. The diff goes into the log so operators
    # tailing the worker output see overnight changes inline.
    # Runs AFTER the digests so a freshly-saved snapshot doesn't sit
    # while delivery work is happening. Same belt-and-braces guard
    # as the rest of main() — the helper itself never raises.
    try:
        run_port_supply_snapshot_job()
    except Exception as exc:
        logger.warning(f"main: port supply snapshot step failed: {exc}")

    # Fan the daily snapshot out across container types — same belt-
    # and-braces guard; per-container failures isolated inside the
    # helper. Runs AFTER the 40FT_DRY single-container save so the
    # default container's diff is always the authoritative signal.
    try:
        run_multi_container_snapshot_job()
    except Exception as exc:
        logger.warning(f"main: multi container snapshot step failed: {exc}")

    # Per-route cargo mix snapshot — feeds CARGO_FLOW_ANOMALY alerts
    # on the next tick once the trailing window populates. Runs in
    # the snapshot block so it shares the same retention + integrity
    # cadence. Never raises.
    try:
        run_cargo_mix_snapshot_job()
    except Exception as exc:
        logger.warning(f"main: cargo mix snapshot step failed: {exc}")

    # Per-ticker risk score snapshot — feeds the company-risk trend
    # UI + band-transition narrations. Runs alongside the cargo mix
    # snapshot so both daily history streams stay in sync. Never raises.
    try:
        run_company_risk_snapshot_job()
    except Exception as exc:
        logger.warning(f"main: company risk snapshot step failed: {exc}")

    # Sweep recent snapshot dirs for missing or corrupted files. Runs
    # AFTER all snapshot writes so anything wrong is caught the same
    # tick that produced it; the helper logs the count to the worker
    # output so operators see "X of Y healthy" inline.
    try:
        run_snapshot_integrity_check_job()
    except Exception as exc:
        logger.warning(f"main: snapshot integrity check step failed: {exc}")

    # Prune old port-supply snapshot dirs per the retention policy.
    # Runs LAST among the snapshot steps so today's writes are never
    # GC'd by the same tick that produced them. Helper never raises.
    try:
        run_port_supply_snapshot_gc_job()
    except Exception as exc:
        logger.warning(f"main: port supply snapshot gc step failed: {exc}")

    # User-configured report schedules. Runs AFTER the operator digest
    # so a freshly-generated scheduled report can ride the same data
    # bundle the digest just read. Same belt-and-braces guard — the
    # helper already swallows per-schedule errors and never raises.
    try:
        sched_summary = run_report_scheduler_job()
        logger.info(
            f"main: report scheduler fired={sched_summary.get('fired', 0)} "
            f"succeeded={sched_summary.get('succeeded', 0)} "
            f"failed={sched_summary.get('failed', 0)}"
        )
    except Exception as exc:
        logger.warning(f"main: report scheduler step failed: {exc}")

    exit_code = 0 if result.success else 1
    sys.exit(exit_code)


if __name__ == "__main__":  # pragma: no cover
    main()
