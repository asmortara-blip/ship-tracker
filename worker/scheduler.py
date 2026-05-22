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
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger


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

    exit_code = 0 if result.success else 1
    sys.exit(exit_code)


if __name__ == "__main__":  # pragma: no cover
    main()
