"""tab_worker_health.py — operator-facing dashboard for background jobs.

The ``worker/scheduler.py`` module runs ~15 background jobs in one cron
pass (briefing, pruning, health pings, source-health alerting, perf-budget
checks, etc.) and each job's return value is logged to stdout then dropped
on the floor. Operators today have to grep ``logs/scheduler.log`` to
answer "did the prune fire last night, and what did it return?".

This tab is the surface for those persisted runs. Every wrapped
``run_*_job`` in the scheduler records a row via ``state.worker_runs``;
this tab summarises every known job and surfaces the most-recent 50 runs
with full result/error payloads on demand.

Sections
--------
Hero          — 4 KPIs (total jobs known, jobs OK in last 24h, jobs ERROR
                in last 24h, avg duration across recent runs).
Job overview  — one row per known job: name, last status, last run at,
                last duration, last result summary, success rate (24h).
Recent runs   — most-recent 50 runs across all jobs, descending by
                started_at; per-row expander shows full result_json +
                error_message.

Auto-refresh
------------
A checkbox at the top toggles a 60-second auto-refresh. When ticked, the
tab re-runs the Streamlit script every 60 seconds (``st.session_state``
holds the last-refresh wall-clock so we don't refresh more often than
the toggle period). No JS timer — we lean on Streamlit's built-in
``st.experimental_rerun`` / ``st.rerun`` so the refresh stays
server-side.

Defensive contract
------------------
Every panel is wrapped in its own try/except with an ``st.warning``
fallback so a single bad row (e.g. malformed result_json) degrades to
"panel unavailable" instead of blanking the whole tab. Engine imports
are lazy inside ``render()`` so the smoke harness can import this module
even with a broken telemetry stack.
"""
from __future__ import annotations

import json
import time
from typing import Any

import pandas as pd
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT3,
    metric_card_row,
    page_header,
    section_divider,
)


# ── Small formatting helpers ────────────────────────────────────────────────


def _fmt_duration(seconds: float) -> str:
    """Render a duration in seconds; falls back to '—' on non-numeric input."""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if s <= 0.0:
        return "—"
    if s < 1.0:
        return f"{int(s * 1000):,} ms"
    if s < 60.0:
        return f"{s:.2f}s"
    return f"{s / 60.0:.1f}m"


def _fmt_ts(iso: str) -> str:
    """Render an ISO timestamp; falls back to the raw first-16 chars when
    the user-tz helper isn't available."""
    try:
        from utils.tz import format_user_tz
        out = format_user_tz(iso, fmt="%Y-%m-%d %H:%M")
        if out:
            return out
    except Exception:
        pass
    try:
        return str(iso)[:16].replace("T", " ")
    except Exception:
        return ""


def _status_pill(status: str) -> str:
    """Render the status as a coloured ✓/✗ glyph for table display."""
    s = (status or "").lower()
    if s == "ok":
        return "✓ OK"
    if s == "error":
        return "✗ ERROR"
    if s == "never":
        return "— NEVER"
    return str(status).upper() or "—"


def _status_color(status: str) -> str:
    s = (status or "").lower()
    if s == "ok":
        return C_HIGH
    if s == "error":
        return C_LOW
    if s == "never":
        return C_TEXT3
    return C_MOD


def _pretty_json(blob: str) -> str:
    """Pretty-print a JSON string for the expandable details panel.

    Falls back to the raw string on parse failure — the operator still
    sees something even if the payload was hand-edited or otherwise
    corrupted.
    """
    try:
        parsed = json.loads(blob or "{}")
        return json.dumps(parsed, indent=2, default=str)
    except (TypeError, ValueError, json.JSONDecodeError):
        return str(blob or "")


# ── Engine adapters — each returns a default on any failure ─────────────────
# Lazy imports keep this module importable from the smoke harness even when
# state.worker_runs itself is broken.


def _load_job_summary() -> list[dict]:
    from state.worker_runs import summarize_jobs
    return summarize_jobs() or []


def _load_recent_runs(limit: int = 50) -> list:
    from state.worker_runs import list_recent_runs
    return list_recent_runs(limit=limit) or []


# ── Row builders — pure functions ──────────────────────────────────────────


def _build_overview_rows(summary: list[dict]) -> list[dict]:
    """One presentation row per known job."""
    if not summary:
        return []
    out: list[dict] = []
    for row in summary:
        last_status = row.get("last_status", "NEVER")
        runs_24h = int(row.get("runs_in_window", 0) or 0)
        success_rate = float(row.get("success_rate_24h", 1.0) or 0.0)
        # When runs_in_window=0, success_rate is meaningless ("no data") —
        # render '—' instead of "100%" which would be misleading.
        if runs_24h == 0:
            sr_display = "—"
        else:
            sr_display = f"{success_rate * 100:.0f}%"
        out.append({
            "Job": row.get("job_name", ""),
            "Last status": _status_pill(last_status),
            "Last run": _fmt_ts(row.get("last_run_at", "") or ""),
            "Last duration": _fmt_duration(row.get("last_duration", 0.0)),
            "Last result": row.get("last_result_summary", "") or "—",
            "Runs (24h)": runs_24h,
            "Success (24h)": sr_display,
        })
    return out


def _build_recent_rows(runs: list) -> list[dict]:
    """One presentation row per recent run."""
    if not runs:
        return []
    out: list[dict] = []
    for r in runs:
        out.append({
            "When": _fmt_ts(getattr(r, "started_at", "") or ""),
            "Job": getattr(r, "job_name", "") or "",
            "Status": _status_pill(getattr(r, "status", "") or ""),
            "Duration": _fmt_duration(getattr(r, "duration_seconds", 0.0)),
            "Result": _summarize_recent_result(
                getattr(r, "result_json", "") or "",
                getattr(r, "error_message", None),
            ),
        })
    return out


def _summarize_recent_result(result_json: str, error_message: Any) -> str:
    """One-line summary for the Recent Runs table.

    On error rows, prefer the error message — that's the diagnostic
    operators want to see at a glance. On ok rows, render the result
    dict as ``k=v · k=v`` (same idiom as summarize_jobs).
    """
    if error_message:
        msg = str(error_message)
        return (msg[:120] + "…") if len(msg) > 120 else msg
    try:
        parsed = json.loads(result_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return "—"
    if isinstance(parsed, dict):
        if not parsed:
            return "—"
        if parsed.get("_unencodable"):
            return parsed.get("_repr", "<unrepresentable>")[:120]
        pieces = [f"{k}={v}" for k, v in parsed.items()]
        text = " · ".join(pieces)
        return (text[:120] + "…") if len(text) > 120 else text
    if isinstance(parsed, list):
        return f"list[{len(parsed)}]"
    return str(parsed)[:120]


# ── Hero KPI strip ───────────────────────────────────────────────────────────


def _render_hero(summary: list[dict], recent: list) -> None:
    """4 KPIs derived from the persisted run history."""
    total_jobs = len(summary)

    # Jobs OK vs ERROR in the last 24h — derive from each job's
    # last-24h aggregates so the KPI is per-job, not per-run.
    ok_24h = 0
    error_24h = 0
    for row in summary:
        runs_24h = int(row.get("runs_in_window", 0) or 0)
        if runs_24h == 0:
            continue
        # If success_rate < 1.0 the job has at least one error in-window.
        # That's the operator-relevant signal — count the job once.
        if float(row.get("success_rate_24h", 1.0) or 0.0) >= 1.0:
            ok_24h += 1
        else:
            error_24h += 1

    # Avg duration across the recent (most-recent 50) runs — gives a
    # rough pulse of worker latency. Excludes 0.0 durations which are
    # the "unknown" sentinel from a bad timestamp parse.
    durations = [
        float(getattr(r, "duration_seconds", 0.0) or 0.0)
        for r in recent
    ]
    durations = [d for d in durations if d > 0.0]
    avg_dur = sum(durations) / len(durations) if durations else 0.0

    metric_card_row([
        {
            "label": "JOBS TRACKED",
            "value": f"{total_jobs:,}",
            "accent": C_ACCENT,
            "sublabel": "known by scheduler",
        },
        {
            "label": "OK · 24H",
            "value": f"{ok_24h:,}",
            "accent": C_HIGH if ok_24h > 0 else C_TEXT3,
            "sublabel": "jobs all-green",
        },
        {
            "label": "ERRORS · 24H",
            "value": f"{error_24h:,}",
            "accent": C_LOW if error_24h > 0 else C_HIGH,
            "sublabel": "jobs with ≥1 failure",
        },
        {
            "label": "AVG DURATION",
            "value": _fmt_duration(avg_dur),
            "accent": (
                C_HIGH if avg_dur < 5.0
                else (C_MOD if avg_dur < 30.0 else C_LOW)
            ),
            "sublabel": f"across last {len(recent)} runs",
        },
    ], columns=4)


# ── Job overview table ──────────────────────────────────────────────────────


def _render_overview_panel(summary: list[dict]) -> None:
    """One row per known job with last-run stats + 24h success rate."""
    if not summary:
        st.info("No worker jobs known. The scheduler has not been invoked yet.")
        return

    rows = _build_overview_rows(summary)
    if not rows:
        st.info("No job rows to display.")
        return

    # Render via plain dataframe — the status pill is ASCII so it survives
    # st.dataframe without escaping concerns.
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )

    # Highlight any never-run jobs as a footer caption so an operator
    # notices a missing job at a glance.
    never_run = [r["Job"] for r in rows if "NEVER" in str(r["Last status"])]
    if never_run:
        st.caption(
            f"Never run: {', '.join(never_run)} — schedule has not "
            f"executed these yet."
        )


# ── Recent runs ──────────────────────────────────────────────────────────────


def _render_recent_panel(runs: list) -> None:
    """Most-recent 50 runs with per-row expanders for full payload."""
    if not runs:
        st.info("No recorded job runs yet.")
        return

    rows = _build_recent_rows(runs)
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )

    # Per-row expanders for the full result_json + error_message. Capped
    # at 20 so the page doesn't grow unbounded — operators wanting more
    # depth can pivot to grepping the worker log.
    section_divider("Inspect a run")
    st.caption(
        "Expand any row below to view the full result JSON and error "
        "message. The latest 20 runs are inspectable here."
    )
    for r in runs[:20]:
        run_id = getattr(r, "run_id", "") or ""
        job = getattr(r, "job_name", "") or "unknown"
        status = getattr(r, "status", "") or ""
        when = _fmt_ts(getattr(r, "started_at", "") or "")
        with st.expander(
            f"{_status_pill(status)}  ·  {job}  ·  {when}",
            expanded=False,
        ):
            err = getattr(r, "error_message", None)
            if err:
                st.error(f"Error: {err}")
            payload = _pretty_json(getattr(r, "result_json", "") or "")
            st.code(payload, language="json")
            dur = _fmt_duration(getattr(r, "duration_seconds", 0.0))
            st.caption(
                f"run_id={run_id[:8]}…  ·  duration={dur}  ·  "
                f"started_at={getattr(r, 'started_at', '')}  ·  "
                f"finished_at={getattr(r, 'finished_at', '')}"
            )


# ── Auto-refresh ─────────────────────────────────────────────────────────────


def _handle_auto_refresh() -> None:
    """Render the auto-refresh toggle and rerun the script every 60s.

    Uses st.session_state to remember the last-refresh wall-clock so
    multiple toggles in the same session don't trigger immediate
    re-renders. Wrapped in try/except — a refresh failure must NEVER
    crash the tab.
    """
    try:
        col_toggle, col_caption = st.columns([1, 3])
        with col_toggle:
            do_refresh = st.checkbox(
                "Auto-refresh every 60s",
                value=False,
                key="worker_health_autorefresh",
            )
        with col_caption:
            if do_refresh:
                st.caption(
                    "Auto-refresh enabled. The tab re-runs every 60 seconds. "
                    "Uncheck to pause."
                )

        if not do_refresh:
            return

        last_key = "worker_health_last_refresh"
        last = st.session_state.get(last_key, 0.0)
        now = time.time()
        # Only trigger a rerun if at least 60s has elapsed since the
        # last one. The widget value would otherwise tight-loop the
        # script every render cycle.
        if now - float(last or 0.0) >= 60.0:
            st.session_state[last_key] = now
            # Prefer the modern st.rerun; fall back to the experimental
            # alias for older Streamlit versions.
            try:
                st.rerun()
            except Exception:
                try:
                    st.experimental_rerun()
                except Exception:
                    pass
    except Exception as exc:
        logger.exception(f"worker_health: auto-refresh handling failed: {exc}")


# ── Main entry point ─────────────────────────────────────────────────────────


def render(*args, **kwargs) -> None:
    """Render the Worker Health tab.

    Accepts ``*args, **kwargs`` so the smoke harness can pass arbitrary
    kwargs. This tab reads everything from ``state.worker_runs`` and
    needs no caller-supplied data.
    """
    try:
        page_header(
            title="Worker Health",
            subtitle="Per-job execution telemetry for every background job.",
            badge_text="OPS",
            badge_color=C_ACCENT,
        )
    except Exception as exc:
        logger.exception(f"worker_health: page header failed: {exc}")

    # ── Auto-refresh toggle ─────────────────────────────────────────────
    try:
        _handle_auto_refresh()
    except Exception as exc:
        logger.exception(f"worker_health: auto-refresh failed: {exc}")

    # ── Load every payload up-front — one snapshot shared across panels.
    summary: list[dict] = []
    recent: list = []
    try:
        summary = _load_job_summary()
    except Exception as exc:
        logger.exception(f"worker_health: summarize_jobs failed: {exc}")
    try:
        recent = _load_recent_runs(limit=50)
    except Exception as exc:
        logger.exception(f"worker_health: list_recent_runs failed: {exc}")

    # ── Hero KPIs ───────────────────────────────────────────────────────
    try:
        _render_hero(summary, recent)
    except Exception as exc:
        logger.exception(f"worker_health: hero render failed: {exc}")
        try:
            st.warning("Hero KPIs unavailable.")
        except Exception:
            pass

    # ── Job overview ────────────────────────────────────────────────────
    try:
        section_divider("Job status overview")
        _render_overview_panel(summary)
    except Exception as exc:
        logger.exception(f"worker_health: overview panel failed: {exc}")
        try:
            st.warning("Job overview panel unavailable.")
        except Exception:
            pass

    # ── Recent runs ─────────────────────────────────────────────────────
    try:
        section_divider("Recent runs")
        _render_recent_panel(recent)
    except Exception as exc:
        logger.exception(f"worker_health: recent runs panel failed: {exc}")
        try:
            st.warning("Recent runs panel unavailable.")
        except Exception:
            pass

    st.caption(
        "Worker runs are recorded by a decorator on each ``run_*_job`` "
        "in ``worker/scheduler.py``. The most-recent 200 runs are kept "
        "in ``kv_state``."
    )
