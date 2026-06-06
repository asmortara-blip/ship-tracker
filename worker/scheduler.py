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

Cadence
-------
``main()`` is safe to invoke FREQUENTLY. The SLA-critical jobs
(source-health alerting, alert escalation, delivery retry) run on EVERY
invocation; the heavy jobs (the briefing build, prunes, snapshots,
digests) self-throttle via per-job kv_state last-run gates (see
``_job_due`` / ``_run_gated``). So a single fast cron satisfies both —
escalation/retry fire within minutes while the heavy jobs run at most
once per their interval (briefing/prunes/snapshots daily, perf-budget +
health-ping hourly, anomaly 6-hourly):

    # Recommended — every 5 minutes; heavy jobs gate themselves.
    */5 * * * * cd /path/to/ship && /usr/bin/python3 -m worker.scheduler --push >> logs/scheduler.log 2>&1

A daily cron still works (every gate is then trivially due each run);
pass ``--force`` to bypass the gates for a manual full run.

The module is intentionally crash-proof: ``run_daily_briefing_job``
catches every exception and always returns a populated
``ReportJobResult``. The CLI exits 0 on success (a within-cadence
skipped briefing counts as success), 1 on failure.

This module must NOT import ``streamlit``. It is invoked outside the
Streamlit process and therefore has no ``st.*`` available.
"""
from __future__ import annotations

import argparse
import functools
import json
import logging
import os
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
#  Daily briefing TLDR — generate once + persist ready-to-send artifacts
# ─────────────────────────────────────────────────────────────────────────────

# Repo-root cache dir for persisted TLDR delivery artifacts (gitignored).
_BRIEFING_TLDR_DIR: Path = (
    Path(__file__).resolve().parent.parent / "cache" / "briefing_tldr"
)


@_track_run
def run_signal_ledger_freeze_job(data_bundle: dict) -> dict:
    """Freeze today's EquityIdeas into the point-in-time signal ledger (R004).

    Rebuilds the idea set from the same daily bundle the briefing uses
    (compute_shipping_stress -> build_exposure_matrix -> score_equity_ideas) and
    freezes each idea AS ISSUED via ``state.signal_ledger.freeze_ideas``.
    Idempotent per (ticker, day, direction) — a re-run inserts nothing. Never
    raises: a failure here must not touch the briefing flow it rides on.
    """
    bundle = data_bundle or {}
    try:
        from processing.shipping_stress_index import compute_shipping_stress
        from processing.exposure_matrix import build_exposure_matrix
        from processing.disruption_cascade import score_equity_ideas
        from state.signal_ledger import freeze_ideas

        stock_data = bundle.get("stock_data", {})
        stress = compute_shipping_stress(
            bundle.get("freight_data", {}), bundle.get("macro_data", {}),
            bundle.get("port_results", []), bundle.get("route_results", []),
        )
        exposure = build_exposure_matrix(stock_data)
        ideas = score_equity_ideas(
            stress, exposure, stock_data, bundle.get("insights", []),
        )
        frozen = freeze_ideas(ideas, stock_data=stock_data)
        logger.info(
            f"run_signal_ledger_freeze_job: froze {frozen} new idea(s) "
            f"of {len(ideas or [])}"
        )
        return {"frozen": int(frozen), "ideas": len(ideas or [])}
    except Exception as exc:  # noqa: BLE001
        logger.error(f"run_signal_ledger_freeze_job failed: {exc}")
        return {"frozen": 0, "ideas": 0, "error": str(exc)}


def run_signal_drawdown_job(data_bundle: dict) -> dict:
    """Mark the signal ledger forward and fire the per-tier drawdown
    kill-switch (B2). Rides on the same daily bundle as the freeze job, so it
    runs right after the freeze with TODAY's closes already in hand.

    Thin wrapper around
    ``engine.signal_drawdown_alerts.check_and_alert_drawdown`` that adds
    logging and shields the caller from any exception. The underlying alerter
    already cooldown-gates each tier (24h) so a chronically-underwater tier
    stays quiet until tomorrow regardless of cadence.

    Returns the count dict shaped like the other alerters: ``{"checked": N,
    "stand_down": N, "alerted": N, "skipped_cooldown": N}`` (all zeros on a
    top-level exception). NEVER raises — a check failure must never block the
    briefing flow it rides on.
    """
    bundle = data_bundle or {}
    try:
        from engine.signal_drawdown_alerts import check_and_alert_drawdown

        counts = check_and_alert_drawdown(bundle.get("stock_data", {}))
        logger.info(
            f"run_signal_drawdown_job: checked={counts.get('checked', 0)} "
            f"stand_down={counts.get('stand_down', 0)} "
            f"alerted={counts.get('alerted', 0)} "
            f"skipped_cooldown={counts.get('skipped_cooldown', 0)}"
        )
        return counts
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"run_signal_drawdown_job: failed: {exc}")
        return {"checked": 0, "stand_down": 0, "alerted": 0, "skipped_cooldown": 0}


def run_forecast_logging_job(data_bundle: dict, *, root=None) -> dict:
    """Append today's route stress forecasts + realized actuals, then pair (R046).

    The predicted-vs-actual tracker only PAIRS + summarizes; the emission side was
    left as this scheduler wiring step. Each daily pass:

      1. rebuilds the SSI + ``forecast_all_stress`` from the same bundle the
         briefing uses, and ``log_forecast``s each route's 7d/30d stress
         projection WITH its R023 interval (``forecast_sigma``) and the
         forecast-time level as the persistence ``baseline_value`` (so R029's
         CRPS/PIT/coverage/skill have something to score);
      2. ``log_actual``s today's realized stress level per route — which is the
         actual a forecast made N days ago (targeting today) pairs against;
      3. pairs + summarizes via ``run_forecast_log_job``.

    Idempotent-friendly: the pair layer dedupes on (lane, date, horizon) /
    (lane, date), so a re-run the same day does not double-count. Never raises —
    a logging failure must not touch the briefing flow it rides on.
    """
    bundle = data_bundle or {}
    try:
        from processing.shipping_stress_index import compute_shipping_stress
        from processing.disruption_forecast import forecast_all_stress
        from processing.forecast_accuracy_tracker import (
            ActualRecord, ForecastRecord, log_actual, log_forecast,
            run_forecast_log_job, should_log_forecast_today,
        )

        if not should_log_forecast_today():
            return {"ok": True, "skipped": True, "forecasts": 0, "actuals": 0}

        stress = compute_shipping_stress(
            bundle.get("freight_data", {}), bundle.get("macro_data", {}),
            bundle.get("port_results", []), bundle.get("route_results", []),
        )
        forecasts = forecast_all_stress(
            bundle.get("freight_data", {}), bundle.get("macro_data", {}),
            bundle.get("route_results", []), stress_report=stress,
        )
        today = datetime.now(timezone.utc).date().isoformat()
        n_fc = n_act = 0
        for f in forecasts or []:
            rid = str(getattr(f, "route_id", "") or "")
            if not rid:
                continue
            cur = float(getattr(f, "current_stress", 0.0) or 0.0)
            sigma = float(getattr(f, "forecast_sigma", 0.0) or 0.0)
            for horizon, value, h_sigma in (
                (30, float(getattr(f, "stress_30d", cur) or 0.0), sigma),
                (7, float(getattr(f, "stress_7d", cur) or 0.0),
                 sigma * (7.0 / 30.0) ** 0.5),
            ):
                log_forecast(ForecastRecord(
                    forecast_date_iso=today, horizon_days=horizon,
                    predicted_value=value, lane_id=rid,
                    predicted_sigma=h_sigma, baseline_value=cur,
                ), root=root)
                n_fc += 1
            log_actual(ActualRecord(
                actual_date_iso=today, actual_value=cur, lane_id=rid,
            ), root=root)
            n_act += 1

        pair = run_forecast_log_job(root=root)
        logger.info(
            f"run_forecast_logging_job: logged {n_fc} forecast(s) + {n_act} "
            f"actual(s); {pair.get('n_pairs', 0)} pair(s) scored"
        )
        return {"ok": True, "forecasts": n_fc, "actuals": n_act,
                "n_pairs": int(pair.get("n_pairs", 0))}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"run_forecast_logging_job: failed: {exc}")
        return {"ok": False, "forecasts": 0, "actuals": 0, "error": str(exc)}


def run_briefing_tldr_job(data_bundle: dict) -> dict:
    """Generate the day's one-paragraph briefing TLDR once and persist
    ready-to-send artifacts (gated by should_send). Never raises.

    This is the canonical once-per-day owner of the single Haiku TLDR
    call: it builds a NarrationContext from the same data bundle the
    investor-report job uses (via the shared
    ``engine.narration_engine.build_narration_context``), generates the
    day-cached DailyNarration + TLDR — priming ``cache/tldr/{date}.json``
    so the UI briefing tab and any CLI hit the cache instead of calling
    Claude — and, when the TLDR carries material signal, writes
    html/text/subject artifacts under ``cache/briefing_tldr/{date}/`` for
    a downstream channel to dispatch.

    Mirrors ``run_port_supply_snapshot_job``: pure persistence of rendered
    artifacts; channel dispatch is a separate, opt-in concern. Returns a
    small status dict (``ok`` / ``source`` / ``persisted`` / ``paths``).
    """
    out = {"ok": False, "source": "", "persisted": False, "dispatched": 0, "paths": {}}
    try:
        from engine.daily_briefing_tldr import generate_tldr
        from engine.narration_engine import (
            build_narration_context,
            generate_daily_narration,
        )

        bundle = data_bundle or {}
        ctx = build_narration_context(
            bundle.get("port_results", []),
            bundle.get("route_results", []),
            bundle.get("freight_data", {}),
            bundle.get("macro_data", {}),
        )
        # Pass the key explicitly so the headless path never reaches for
        # st.secrets; None → generate_* resolve from env or take template.
        api_key = os.getenv("ANTHROPIC_API_KEY") or None
        narration = generate_daily_narration(ctx, api_key=api_key)
        summary = generate_tldr(narration, api_key=api_key)
        out["ok"] = True
        out["source"] = summary.source
        logger.info(
            f"run_briefing_tldr_job: TLDR generated (source={summary.source})"
        )
    except Exception as exc:   # pragma: no cover - defensive
        logger.warning(f"run_briefing_tldr_job: generation failed: {exc}")
        return out

    # ── Gate once, then persist + dispatch (each independently defensive) ──
    # Quiet days (no material signal) produce no artifacts and send
    # nothing, so channels stay quiet and the cache dir stays tidy.
    try:
        from delivery.briefing_tldr import should_send
        material = should_send(summary)
    except Exception:
        material = False
    if not material:
        logger.info(
            "run_briefing_tldr_job: no material TLDR signal — skipping digest"
        )
        return out

    date_iso = str(getattr(narration, "date", "") or "").strip()

    # Persist ready-to-send artifacts. A render / I/O failure here must
    # NEVER fail the job (or block the dispatch step below).
    try:
        from delivery.briefing_tldr import (
            build_subject_line,
            render_html,
            render_plain_text,
        )

        out_dir = _BRIEFING_TLDR_DIR / (date_iso or "undated")
        out_dir.mkdir(parents=True, exist_ok=True)
        html_path = out_dir / "tldr.html"
        text_path = out_dir / "tldr.txt"
        subj_path = out_dir / "tldr.subject.txt"

        html_path.write_text(render_html(summary, date_iso), encoding="utf-8")
        text_path.write_text(
            render_plain_text(summary, date_iso), encoding="utf-8",
        )
        subj_path.write_text(
            build_subject_line(summary, date_iso) + "\n", encoding="utf-8",
        )

        out["persisted"] = True
        out["paths"] = {
            "html": str(html_path),
            "text": str(text_path),
            "subject": str(subj_path),
        }
        logger.info(f"run_briefing_tldr_job: digest persisted to {out_dir}")
    except Exception as exc:   # pragma: no cover - defensive
        logger.warning(f"run_briefing_tldr_job: digest persistence failed: {exc}")

    # Dispatch to opt-in 'briefing-'-prefixed channels (mirrors the
    # operator digest's 'ops-' convention). No such channels exist by
    # default, so nothing is sent unless an operator subscribes one —
    # the same per-channel best-effort loop as run_operator_digest_job.
    try:
        from delivery.briefing_tldr import (
            BRIEFING_CHANNEL_PREFIX,
            send_briefing_tldr,
        )
        from engine.alert_delivery import load_channels

        dispatched = 0
        for channel in load_channels():
            name = getattr(channel, "name", "") or ""
            if not name.startswith(BRIEFING_CHANNEL_PREFIX) or not channel.enabled:
                continue
            try:
                res = send_briefing_tldr(channel, summary, date_iso)
                if getattr(res, "success", False):
                    dispatched += 1
                logger.info(
                    f"run_briefing_tldr_job: channel={name!r} "
                    f"kind={channel.kind} success={res.success}"
                    + (f" error={res.error_msg!r}" if not res.success else "")
                )
            except Exception as exc:
                logger.warning(
                    f"run_briefing_tldr_job: send to {name!r} failed: {exc}"
                )
        out["dispatched"] = dispatched
    except Exception as exc:   # pragma: no cover - defensive
        logger.warning(f"run_briefing_tldr_job: dispatch step failed: {exc}")

    return out


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
def run_cargo_mix_snapshot_gc_job(*, keep_days: int = 90) -> dict:
    """Prune old cargo-mix snapshot dirs — this tree had no GC, so it grew
    one dated dir/day forever.

    Thin wrapper around
    ``processing.cargo_mix_history.gc_old_cargo_mix_snapshots`` adding
    logging + the no-raise contract. Runs AFTER the cargo-mix save so the
    same tick never collects today's write.
    """
    try:
        from processing.cargo_mix_history import gc_old_cargo_mix_snapshots
        out = gc_old_cargo_mix_snapshots(keep_days=keep_days)
    except Exception as exc:   # pragma: no cover - defensive
        logger.warning(f"run_cargo_mix_snapshot_gc_job: top-level failure: {exc}")
        return {"ok": False, "n_dirs_scanned": 0, "n_dirs_deleted": 0,
                "n_bytes_freed": 0, "preserved_artefacts": 0}
    counts = {
        "ok":                  True,
        "n_dirs_scanned":      int(out.get("n_dirs_scanned", 0)),
        "n_dirs_deleted":      int(out.get("n_dirs_deleted", 0)),
        "n_bytes_freed":       int(out.get("n_bytes_freed", 0)),
        "preserved_artefacts": len(out.get("preserved_artefacts", []) or []),
    }
    logger.info(
        f"run_cargo_mix_snapshot_gc_job: scanned={counts['n_dirs_scanned']} "
        f"deleted={counts['n_dirs_deleted']} "
        f"bytes_freed={counts['n_bytes_freed']:,} "
        f"preserved={counts['preserved_artefacts']}"
    )
    return counts


@_track_run
def run_company_risk_snapshot_gc_job(*, keep_days: int = 90) -> dict:
    """Prune old company-risk snapshot dirs — this tree had no GC, so it
    grew one dated dir/day forever.

    Thin wrapper around
    ``processing.company_risk_history.gc_old_company_risk_snapshots``
    adding logging + the no-raise contract. Runs AFTER the company-risk
    save so the same tick never collects today's write.
    """
    try:
        from processing.company_risk_history import gc_old_company_risk_snapshots
        out = gc_old_company_risk_snapshots(keep_days=keep_days)
    except Exception as exc:   # pragma: no cover - defensive
        logger.warning(f"run_company_risk_snapshot_gc_job: top-level failure: {exc}")
        return {"ok": False, "n_dirs_scanned": 0, "n_dirs_deleted": 0,
                "n_bytes_freed": 0, "preserved_artefacts": 0}
    counts = {
        "ok":                  True,
        "n_dirs_scanned":      int(out.get("n_dirs_scanned", 0)),
        "n_dirs_deleted":      int(out.get("n_dirs_deleted", 0)),
        "n_bytes_freed":       int(out.get("n_bytes_freed", 0)),
        "preserved_artefacts": len(out.get("preserved_artefacts", []) or []),
    }
    logger.info(
        f"run_company_risk_snapshot_gc_job: scanned={counts['n_dirs_scanned']} "
        f"deleted={counts['n_dirs_deleted']} "
        f"bytes_freed={counts['n_bytes_freed']:,} "
        f"preserved={counts['preserved_artefacts']}"
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


@_track_run
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

# ── Per-job cadence gates ─────────────────────────────────────────────────
#
# main() runs the full job list on every invocation. The SLA-critical jobs
# (source-health alerting, alert escalation, delivery retry) must fire on a
# FAST (~5 min) cadence so an unacked CRITICAL escalates / a transient
# delivery failure retries within minutes. The HEAVY jobs (the briefing
# build, prunes, snapshots, digests) must NOT re-run that often. The old
# all-jobs-every-invocation design forced a single cron to choose between the
# two — a daily cron left escalation/retry up to ~24h late.
#
# Fix: gate the heavy jobs on a kv_state last-run timestamp so each runs at
# most once per its interval no matter how often main() fires, while the SLA
# jobs are left ungated (every pass). A single ``*/5 * * * *`` cron then
# satisfies both: the SLA jobs run every 5 min, the heavy jobs self-throttle.
_DAILY_SECONDS = 24 * 60 * 60
_HOURLY_SECONDS = 60 * 60
_SIX_HOURLY_SECONDS = 6 * 60 * 60

_JOB_LASTRUN_KEY = "scheduler:lastrun:{name}"


def _job_due(
    name: str,
    interval_seconds: int,
    *,
    now: Optional[datetime] = None,
    force: bool = False,
) -> bool:
    """True iff job ``name`` is due (>= ``interval_seconds`` since its last
    run, or never run) — and STAMP the run time when returning True.

    Backed by a kv_state row ``scheduler:lastrun:<name>``. ``force`` bypasses
    the gate (still stamps). Fail-OPEN: on ANY error this returns True — better
    to run a heavy job an extra time than to silently stop running it because
    the gate's own bookkeeping broke.
    """
    now_dt = now or datetime.now(timezone.utc)
    key = _JOB_LASTRUN_KEY.format(name=name)
    try:
        from state.db import get_connection

        conn = get_connection()
        if not force:
            row = conn.execute(
                "SELECT value FROM kv_state WHERE key = ?", (key,),
            ).fetchone()
            if row is not None:
                try:
                    last = datetime.fromisoformat(row["value"])
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    last = None
                if last is not None and (
                    now_dt - last
                ).total_seconds() < interval_seconds:
                    return False
        conn.execute(
            "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (key, now_dt.isoformat(), now_dt.isoformat()),
        )
        return True
    except Exception as exc:  # noqa: BLE001 — gate must never stop the worker
        logger.warning(
            f"_job_due: gate check failed for {name!r}, running anyway: {exc}"
        )
        return True


def _run_gated(
    name: str,
    interval_seconds: int,
    fn: Callable,
    *,
    now: Optional[datetime] = None,
    force: bool = False,
    label: Optional[str] = None,
) -> None:
    """Run heavy job ``fn`` iff ``_job_due`` says so; swallow + log any error.

    Skipping (within-interval) is logged at DEBUG; a job exception is logged
    at WARNING. The job itself already no-raises — this is belt-and-braces."""
    if not _job_due(name, interval_seconds, now=now, force=force):
        logger.debug(f"main: {label or name} skipped (within cadence)")
        return
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"main: {label or name} step failed: {exc}")


def _run_always(label: str, fn: Callable) -> None:
    """Run an ungated job (SLA every-pass, or one that self-gates internally);
    swallow + log any error."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"main: {label} step failed: {exc}")


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
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Bypass the per-job cadence gates and run every job this pass "
            "(for a manual full run)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    """CLI entry point. Returns the process exit code (0 success, 1 failure)."""
    args = _parse_args(argv)
    now = datetime.now(timezone.utc)
    force = getattr(args, "force", False)

    # Daily briefing — the headline build. Gated daily (the LLM report must
    # not rebuild on every fast pass) with a LAZY bundle load so a skipped
    # pass doesn't pay load_data_bundle()'s cost. ``result`` stays None when
    # skipped and is treated as success for the exit code.
    result = None
    if _job_due("run_daily_briefing_job", _DAILY_SECONDS, now=now, force=force):
        bundle = load_data_bundle()
        result = run_daily_briefing_job(bundle, push_to_channels=args.push)
        print(json.dumps(asdict(result), indent=2, default=str))
        # Daily briefing TLDR — primes the day-cache the UI reads + persists
        # ready-to-send artifacts, reusing the freshly-loaded bundle. A TLDR
        # failure must never touch the report.
        _run_always("briefing TLDR", lambda: run_briefing_tldr_job(bundle))
        # Freeze today's EquityIdeas into the point-in-time signal ledger
        # (R004) — reuse the freshly-loaded bundle; idempotent per day so it's
        # safe to run on every daily-gated briefing pass.
        _run_always("signal ledger freeze",
                    lambda: run_signal_ledger_freeze_job(bundle))
        # Per-tier drawdown kill-switch (B2) — mark the freshly-frozen ledger
        # forward on TODAY's closes and fire a SIGNAL_DRAWDOWN alert for any
        # conviction tier whose track record cratered. Runs right after the
        # freeze; the alerter self-gates each tier with a 24h cooldown.
        _run_always("signal drawdown kill-switch",
                    lambda: run_signal_drawdown_job(bundle))
        # Predicted-vs-actual forecast logging (R046) — emit today's route stress
        # forecasts (with R023 intervals) + realized actuals, then pair, so the
        # R029 calibration scores accrue real history. Reuses the daily bundle.
        _run_always("forecast logging",
                    lambda: run_forecast_logging_job(bundle))
    else:
        print(json.dumps(
            {"briefing": "skipped", "reason": "ran within the daily interval"}
        ))

    # Daily retention prunes — gated so a fast cron doesn't re-prune 288×/day.
    _run_gated("run_telemetry_prune_job", _DAILY_SECONDS, run_telemetry_prune_job,
               now=now, force=force, label="telemetry prune")
    _run_gated("run_perf_prune_job", _DAILY_SECONDS, run_perf_prune_job,
               now=now, force=force, label="perf prune")
    _run_gated("run_snapshot_prune_job", _DAILY_SECONDS, run_snapshot_prune_job,
               now=now, force=force, label="snapshot prune")

    # Data-source health: ping hourly, prune the ping rows daily.
    _run_gated("run_health_ping_job", _HOURLY_SECONDS, run_health_ping_job,
               now=now, force=force, label="health ping")
    _run_gated("run_health_prune_job", _DAILY_SECONDS, run_health_prune_job,
               now=now, force=force, label="health prune")

    # SLA job — EVERY pass: classify the freshest data-source snapshot so a
    # degradation alert fires within minutes.
    _run_always("source health alert", run_source_health_alert_job)

    # Per-tab perf-budget check (hourly) + anomaly detection (6-hourly; the
    # per-metric 24h cooldown de-dupes regardless of cadence).
    _run_gated("run_perf_budget_check_job", _HOURLY_SECONDS, run_perf_budget_check_job,
               now=now, force=force, label="perf budget check")
    _run_gated("run_anomaly_detection_job", _SIX_HOURLY_SECONDS, run_anomaly_detection_job,
               now=now, force=force, label="anomaly detection")

    # SLA jobs — EVERY pass: an unacked CRITICAL escalates, and a transient
    # delivery failure retries, within minutes. These are the reason main()
    # must be safe to invoke every ~5 minutes.
    _run_always("alert escalation", run_alert_escalation_job)
    _run_always("delivery retry", run_delivery_retry_job)

    # Daily retention / cleanup prunes.
    _run_gated("run_bulk_export_prune_job", _DAILY_SECONDS, run_bulk_export_prune_job,
               now=now, force=force, label="bulk export prune")
    _run_gated("run_alert_prune_job", _DAILY_SECONDS, run_alert_prune_job,
               now=now, force=force, label="alert prune")
    _run_gated("run_silence_cleanup_job", _DAILY_SECONDS, run_silence_cleanup_job,
               now=now, force=force, label="silence cleanup")
    _run_gated("run_audit_prune_job", _DAILY_SECONDS, run_audit_prune_job,
               now=now, force=force, label="audit prune")
    _run_gated("run_report_prune_job", _DAILY_SECONDS, run_report_prune_job,
               now=now, force=force, label="report prune")

    # Operator daily digest (gated daily); weekly digest self-gates on the
    # per-user day/hour + a kv lock, so it runs every pass and decides
    # internally whether to send.
    _run_gated("run_operator_digest_job", _DAILY_SECONDS, run_operator_digest_job,
               now=now, force=force, label="operator digest")
    _run_always("weekly digest", run_weekly_digest_job_wrapper)

    # Daily snapshot pipeline (write → multi-container fan-out → cargo-mix +
    # company-risk history → integrity sweep → GC every tree), in dependency
    # order. Each snapshot tree's GC runs AFTER its save so the same tick
    # never collects today's write.
    _run_gated("run_port_supply_snapshot_job", _DAILY_SECONDS, run_port_supply_snapshot_job,
               now=now, force=force, label="port supply snapshot")
    _run_gated("run_multi_container_snapshot_job", _DAILY_SECONDS, run_multi_container_snapshot_job,
               now=now, force=force, label="multi container snapshot")
    _run_gated("run_cargo_mix_snapshot_job", _DAILY_SECONDS, run_cargo_mix_snapshot_job,
               now=now, force=force, label="cargo mix snapshot")
    _run_gated("run_company_risk_snapshot_job", _DAILY_SECONDS, run_company_risk_snapshot_job,
               now=now, force=force, label="company risk snapshot")
    _run_gated("run_snapshot_integrity_check_job", _DAILY_SECONDS, run_snapshot_integrity_check_job,
               now=now, force=force, label="snapshot integrity check")
    _run_gated("run_port_supply_snapshot_gc_job", _DAILY_SECONDS, run_port_supply_snapshot_gc_job,
               now=now, force=force, label="port supply snapshot gc")
    _run_gated("run_cargo_mix_snapshot_gc_job", _DAILY_SECONDS, run_cargo_mix_snapshot_gc_job,
               now=now, force=force, label="cargo mix snapshot gc")
    _run_gated("run_company_risk_snapshot_gc_job", _DAILY_SECONDS, run_company_risk_snapshot_gc_job,
               now=now, force=force, label="company risk snapshot gc")

    # User-configured report schedules — self-gates per schedule via
    # next_run_at, so it runs every pass and fires only what's due.
    try:
        sched_summary = run_report_scheduler_job()
        logger.info(
            f"main: report scheduler fired={sched_summary.get('fired', 0)} "
            f"succeeded={sched_summary.get('succeeded', 0)} "
            f"failed={sched_summary.get('failed', 0)}"
        )
    except Exception as exc:
        logger.warning(f"main: report scheduler step failed: {exc}")

    # A skipped (within-cadence) briefing is success, not failure.
    exit_code = 0 if (result is None or result.success) else 1
    sys.exit(exit_code)


if __name__ == "__main__":  # pragma: no cover
    main()
