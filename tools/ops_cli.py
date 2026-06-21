"""``python -m tools.ops`` — operator CLI for common admin tasks.

The Streamlit UI fronts every domain action (acknowledge an alert,
prune telemetry, list channels, mint an API token, …), but the same
actions are sometimes needed from a shell — say, a colleague has SSH'd
into the container after the UI started misbehaving and needs to nuke
a corrupt rule, or an automation script needs to bulk-acknowledge
overnight alerts before a market open.

This module exposes one subcommand per common operation. Each handler
is a thin wrapper around an existing ``engine.``/``utils.``/``auth.``
function — there is no business logic here, only argument parsing and
output formatting.

Output modes
------------
Every subcommand accepts ``--json``. Without it the handler prints a
small ASCII table or one-line summary; with it the handler prints
``json.dumps(payload, indent=2, default=str)``. ``default=str`` is
load-bearing — many of the wrapped functions return objects containing
``datetime`` instances that the stdlib JSON encoder cannot serialise.

Exit codes
----------
* ``0`` — success
* ``1`` — handler raised; the exception message went to stderr
* ``2`` — argparse rejected the CLI invocation (missing / unknown flag)

The CLI must NEVER bubble an exception out to the shell. Every handler
is wrapped in a ``try / except Exception`` that turns the failure into
an exit-1 with a one-line stderr message. Tests rely on this contract.

Logging
-------
Domain modules log via loguru, which the project routes to stderr; we
inherit that behaviour. The CLI itself prints results to STDOUT and
never logs to stdout — stdout is reserved for JSON / table output that
operators may pipe into ``jq`` or ``column``.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional


# ─────────────────────────────────────────────────────────────────────────────
#  Output helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_jsonable(obj: Any) -> Any:
    """Recursively turn dataclasses / Paths / datetimes into JSON-friendly
    primitives. The stdlib ``json.dumps(..., default=str)`` would also
    work, but going through ``asdict`` first means nested dataclasses
    inside a list survive untouched (and we can run keys through
    ``str()`` if a column name happens to be e.g. an int)."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(x) for x in obj]
    # Fallback — let json.dumps(default=str) catch this if it reaches it.
    return obj


def _print_json(payload: Any) -> None:
    print(json.dumps(_to_jsonable(payload), indent=2, default=str))


def _print_table(rows: list[dict], columns: Optional[list[str]] = None) -> None:
    """Render ``rows`` as a fixed-width ASCII table on stdout.

    No external dependency — the project's hot-path UI tests assert the
    CLI works in a clean container with only stdlib + the runtime deps.
    Empty input prints a one-line "(no rows)" so a pipe consumer can
    tell apart "empty result" from "command crashed".
    """
    if not rows:
        print("(no rows)")
        return

    cols = columns if columns is not None else list(rows[0].keys())
    # Compute widths from the header AND every row value.
    widths: dict[str, int] = {}
    for c in cols:
        max_val_len = max((len(str(r.get(c, ""))) for r in rows), default=0)
        widths[c] = max(len(str(c)), max_val_len)

    def _fmt_row(values: list[str]) -> str:
        return "  ".join(values[i].ljust(widths[cols[i]]) for i in range(len(cols)))

    header = _fmt_row([str(c) for c in cols])
    sep = "  ".join("-" * widths[c] for c in cols)
    print(header)
    print(sep)
    for r in rows:
        print(_fmt_row([str(r.get(c, "")) for c in cols]))


def _print_kv(payload: dict) -> None:
    """Render a flat dict as ``key: value`` lines, sorted by insertion
    order. Used by ``status``, ``telemetry usage``, ``perf summary``,
    and other handlers that return a single summary dict."""
    if not payload:
        print("(empty)")
        return
    width = max(len(str(k)) for k in payload.keys())
    for k, v in payload.items():
        print(f"{str(k).ljust(width)} : {v}")


# ─────────────────────────────────────────────────────────────────────────────
#  Subcommand handlers
#
#  Each handler takes the argparse Namespace and returns nothing. They
#  print to stdout. Exceptions propagate to ``main`` which catches them
#  and returns exit code 1.
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_status(args: argparse.Namespace) -> None:
    """Headline numbers — schema version + counts. Cheap; runs against
    the live DB so an operator can sanity-check the deployment."""
    from state.db import SCHEMA_VERSION
    from auth.users import count_users
    from engine.alert_engine_v2 import load_alerts, get_unread_count
    from engine.alert_delivery import load_channels

    alerts = load_alerts(max_age_days=30)
    payload = {
        "schema_version": int(SCHEMA_VERSION),
        "user_count": int(count_users()),
        "alerts_30d": len(alerts),
        "alerts_unread": int(get_unread_count()),
        "channels": len(load_channels()),
    }
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_alerts_list(args: argparse.Namespace) -> None:
    from engine.alert_engine_v2 import load_alerts

    alerts = load_alerts(max_age_days=30)
    if args.severity:
        alerts = [a for a in alerts if a.severity == args.severity]
    alerts = alerts[: max(1, int(args.limit))]

    if args.json:
        _print_json(alerts)
        return
    rows = [
        {
            "alert_id":   a.alert_id[:8],
            "created_at": a.created_at,
            "severity":   a.severity,
            "type":       a.alert_type,
            "ack":        "Y" if a.acknowledged else "N",
            "title":      (a.title[:60] + "…") if len(a.title) > 60 else a.title,
        }
        for a in alerts
    ]
    _print_table(rows, columns=["alert_id", "created_at", "severity", "type", "ack", "title"])


def _cmd_alerts_ack(args: argparse.Namespace) -> None:
    from engine.alert_engine_v2 import acknowledge_alert

    acknowledge_alert(args.alert_id)
    if args.json:
        _print_json({"acknowledged": args.alert_id})
    else:
        print(f"acknowledged: {args.alert_id}")


def _cmd_alerts_ack_all(args: argparse.Namespace) -> None:
    from engine.alert_engine_v2 import acknowledge_all, get_unread_count

    before = get_unread_count()
    acknowledge_all()
    after = get_unread_count()
    payload = {"unread_before": before, "unread_after": after, "acked": before - after}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_alerts_metrics(args: argparse.Namespace) -> None:
    from engine.alert_analytics import compute_alert_metrics

    metrics = compute_alert_metrics(window_days=int(args.window))
    if args.json:
        _print_json(metrics)
        return
    # Pretty-print the headline numbers; full breakdown is JSON-only.
    headline = {
        "window_days":              int(args.window),
        "total_alerts":             metrics.total_alerts,
        "acknowledged_count":       metrics.acknowledged_count,
        "unacknowledged_count":     metrics.unacknowledged_count,
        "ack_rate":                 round(metrics.ack_rate, 4),
        "median_time_to_ack_hours": metrics.median_time_to_ack_hours,
    }
    _print_kv(headline)


def _cmd_channels_list(args: argparse.Namespace) -> None:
    from engine.alert_delivery import load_channels

    channels = load_channels()
    if args.json:
        _print_json(channels)
        return
    rows = [
        {
            "channel_id":   c.channel_id[:8],
            "name":         c.name,
            "kind":         c.kind,
            "threshold":    c.severity_threshold,
            "enabled":      "Y" if c.enabled else "N",
            "digest_mode":  c.digest_mode,
        }
        for c in channels
    ]
    _print_table(rows, columns=["channel_id", "name", "kind", "threshold", "enabled", "digest_mode"])


def _cmd_channels_delete(args: argparse.Namespace) -> None:
    from engine.alert_delivery import delete_channel

    delete_channel(args.channel_id)
    if args.json:
        _print_json({"deleted": args.channel_id})
    else:
        print(f"deleted: {args.channel_id}")


def _cmd_channels_usage(args: argparse.Namespace) -> None:
    """Print per-channel monthly delivery usage for ``--user-id``.

    Renders one row per channel: budget, usage, pct, over_budget. Helpful
    for "is my Slack channel running near its cap?" introspection without
    opening the UI. JSON mode preserves the full dict shape from
    :func:`engine.alert_delivery.get_all_channel_usage`.
    """
    from engine.alert_delivery import get_all_channel_usage

    rows = get_all_channel_usage(user_id=args.user_id or "")
    if args.json:
        _print_json(rows)
        return
    if not rows:
        print("(no channels)")
        return
    table_rows = []
    for u in rows:
        budget = int(u.get("budget", 0) or 0)
        usage = int(u.get("usage", 0) or 0)
        pct = u.get("pct")
        if budget <= 0:
            budget_label = "unlimited"
            pct_label = "—"
        else:
            budget_label = str(budget)
            pct_label = f"{pct:.0f}%" if pct is not None else "—"
        table_rows.append({
            "channel_id":  (u.get("channel_id") or "")[:8],
            "name":        u.get("name", ""),
            "kind":        u.get("kind", ""),
            "budget":      budget_label,
            "usage":       str(usage),
            "pct":         pct_label,
            "over_budget": "Y" if u.get("over_budget") else "N",
        })
    _print_table(
        table_rows,
        columns=["channel_id", "name", "kind", "budget", "usage", "pct", "over_budget"],
    )


def _cmd_channels_reset_usage(args: argparse.Namespace) -> None:
    """Zero the per-channel monthly counter for ``--user-id``. Used after
    a noisy week ended early — the channel can resume deliveries before
    the natural month-boundary reset.
    """
    from engine.alert_delivery import reset_channel_usage

    ok = reset_channel_usage(args.channel_id, user_id=args.user_id or "")
    if args.json:
        _print_json({"channel_id": args.channel_id, "reset": ok})
    else:
        if ok:
            print(f"reset: {args.channel_id}")
        else:
            print(f"reset failed: {args.channel_id}")


def _cmd_channels_failures(args: argparse.Namespace) -> None:
    """Print per-channel consecutive-failure counts for ``--user-id``.

    One row per channel: failure count, whether it's at the auto-disable
    threshold, and whether the auto-disabled flag is set. Helpful for
    "is anything about to trip the breaker?" introspection without
    opening the UI. JSON mode preserves the full dict shape.
    """
    from engine.alert_delivery import (
        AUTO_DISABLE_THRESHOLD,
        get_consecutive_failures,
        is_auto_disabled,
        load_channels,
    )

    uid = args.user_id or ""
    try:
        channels = load_channels(user_id=uid)
    except Exception:
        channels = []
    out_rows = []
    for ch in channels:
        try:
            count = int(get_consecutive_failures(ch.channel_id, user_id=uid))
        except Exception:
            count = 0
        try:
            auto_off = bool(is_auto_disabled(ch.channel_id, user_id=uid))
        except Exception:
            auto_off = False
        out_rows.append({
            "channel_id":    ch.channel_id,
            "name":          ch.name,
            "kind":          ch.kind,
            "enabled":       bool(ch.enabled),
            "failures":      count,
            "threshold":     int(AUTO_DISABLE_THRESHOLD),
            "auto_disabled": auto_off,
        })

    if args.json:
        _print_json(out_rows)
        return
    if not out_rows:
        print("(no channels)")
        return
    table_rows = []
    for r in out_rows:
        table_rows.append({
            "channel_id":    (r["channel_id"] or "")[:8],
            "name":          r["name"],
            "kind":          r["kind"],
            "enabled":       "Y" if r["enabled"] else "N",
            "failures":      f"{r['failures']}/{r['threshold']}",
            "auto_disabled": "Y" if r["auto_disabled"] else "N",
        })
    _print_table(
        table_rows,
        columns=[
            "channel_id", "name", "kind", "enabled", "failures",
            "auto_disabled",
        ],
    )


def _cmd_channels_reset_failures(args: argparse.Namespace) -> None:
    """Zero the per-channel consecutive-failure counter for
    ``--user-id``. Mirrors the ``reset-usage`` subcommand. Also clears
    the auto-disabled flag so the UI stops nagging.
    """
    from engine.alert_delivery import reset_consecutive_failures

    ok = reset_consecutive_failures(args.channel_id, user_id=args.user_id or "")
    if args.json:
        _print_json({"channel_id": args.channel_id, "reset": ok})
    else:
        if ok:
            print(f"reset failures: {args.channel_id}")
        else:
            print(f"reset failed: {args.channel_id}")


def _cmd_channels_set_budget(args: argparse.Namespace) -> None:
    """Update the ``monthly_budget`` column for a single channel. Looks
    up the channel via :func:`load_channels` (per-user scoping applies),
    mutates the in-memory dataclass, then re-saves through
    :func:`save_channel`. The save is an UPSERT so the rest of the row
    is preserved untouched.

    A missing channel id prints a ``channel not found`` line + raises
    ``SystemExit(1)`` so a wrapping shell script notices via the exit
    code. Other handlers in this module already use the same pattern
    (see ``_cmd_users_create``).
    """
    from engine.alert_delivery import load_channels, save_channel

    channels = load_channels(user_id=args.user_id or "")
    match = next((c for c in channels if c.channel_id == args.channel_id), None)
    if match is None:
        if args.json:
            _print_json({"error": "channel not found", "channel_id": args.channel_id})
        else:
            print(f"channel not found: {args.channel_id}")
        # Soft-exit (non-zero) so a wrapping script notices.
        raise SystemExit(1)
    try:
        new_budget = max(0, int(args.budget))
    except (TypeError, ValueError):
        new_budget = 0
    match.monthly_budget = new_budget
    save_channel(match, user_id=args.user_id or "")
    if args.json:
        _print_json({"channel_id": args.channel_id, "monthly_budget": new_budget})
    else:
        print(f"updated: {args.channel_id} monthly_budget={new_budget}")


def _cmd_reports_list(args: argparse.Namespace) -> None:
    from utils.report_history import list_reports

    reports = list_reports()
    reports = reports[: max(1, int(args.limit))]
    if args.json:
        _print_json(reports)
        return
    rows = [
        {
            "report_id":    r.report_id[:8],
            "generated_at": r.generated_at,
            "sentiment":    r.sentiment_label,
            "risk_level":   r.risk_level,
            "size_kb":      f"{r.file_size_kb:.1f}",
        }
        for r in reports
    ]
    _print_table(rows, columns=["report_id", "generated_at", "sentiment", "risk_level", "size_kb"])


def _cmd_reports_delete(args: argparse.Namespace) -> None:
    from utils.report_history import delete_report

    ok = delete_report(args.report_id)
    payload = {"deleted": args.report_id, "success": bool(ok)}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_reports_stats(args: argparse.Namespace) -> None:
    from utils.report_history import get_report_stats

    stats = get_report_stats()
    if args.json:
        _print_json(stats)
    else:
        _print_kv(stats)


def _cmd_reports_diff(args: argparse.Namespace) -> None:
    """Compare two reports by id and print the structured diff.

    ``--format md`` (default) prints the Markdown rendering — most
    useful when piping into a pager or a chat client. ``--format json``
    prints the same structured payload the API serves so an automation
    script can consume it directly. ``--user-id`` honours per-user
    scoping (a user can only diff reports they own); without it the
    legacy empty-scope behaviour applies.

    Exit codes:
      * 0 on success
      * 1 if either report id is unknown in the caller's scope (no
        stack trace — just a one-line stderr message)
    """
    from utils.report_diff import (
        diff_reports,
        diff_to_dict,
        load_report_payload,
        render_diff_markdown,
    )

    user_id = getattr(args, "user_id", None) or None
    a_id = args.report_id_a
    b_id = args.report_id_b

    payload_a = load_report_payload(a_id, user_id=user_id)
    payload_b = load_report_payload(b_id, user_id=user_id)
    if payload_a is None or payload_b is None:
        # Same indistinguishable failure as the API — don't tell the
        # operator WHICH of the two is missing, only that one of them
        # is. Surfacing "id X is unknown" would leak existence info
        # across users in a multi-tenant install.
        print(
            f"reports diff: one or both report ids unknown in scope "
            f"(a={a_id!r}, b={b_id!r})",
            file=sys.stderr,
        )
        raise SystemExit(1)

    diff = diff_reports(
        payload_a, payload_b,
        report_a_id=a_id, report_b_id=b_id,
    )

    fmt = getattr(args, "format", "md") or "md"
    if fmt == "json":
        _print_json(diff_to_dict(diff))
    else:
        # Default + any non-json value falls back to Markdown — the
        # CLI's "give me something readable" contract.
        print(render_diff_markdown(diff))


def _cmd_telemetry_usage(args: argparse.Namespace) -> None:
    from engine.llm_telemetry import get_usage_summary

    summary = get_usage_summary(window_days=int(args.window))
    if args.json:
        _print_json(summary)
        return
    # Strip nested dicts for the table view — operators get the headline.
    headline = {
        "window_days":      summary.get("window_days"),
        "total_calls":      summary.get("total_calls"),
        "total_tokens_in":  summary.get("total_tokens_in"),
        "total_tokens_out": summary.get("total_tokens_out"),
        "total_cost_usd":   round(float(summary.get("total_cost_usd", 0.0)), 6),
    }
    _print_kv(headline)


def _cmd_telemetry_recent(args: argparse.Namespace) -> None:
    from engine.llm_telemetry import get_recent_calls

    calls = get_recent_calls(limit=int(args.limit))
    if args.json:
        _print_json(calls)
        return
    rows = [
        {
            "created_at":  c.get("created_at", ""),
            "source":      c.get("source", ""),
            "tab_name":    c.get("tab_name", ""),
            "model":       c.get("model", ""),
            "tokens_in":   c.get("tokens_in", 0),
            "tokens_out":  c.get("tokens_out", 0),
            "cost":        f"{float(c.get('est_cost_usd', 0.0)):.6f}",
        }
        for c in calls
    ]
    _print_table(rows, columns=["created_at", "source", "tab_name", "model", "tokens_in", "tokens_out", "cost"])


def _cmd_telemetry_prune(args: argparse.Namespace) -> None:
    from engine.llm_telemetry import prune_old_calls

    n = prune_old_calls(retention_days=int(args.retention))
    payload = {"retention_days": int(args.retention), "deleted_rows": int(n)}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_perf_summary(args: argparse.Namespace) -> None:
    from engine.perf_telemetry import get_perf_summary

    summary = get_perf_summary(window_hours=int(args.window_hours))
    if args.json:
        _print_json(summary)
        return
    headline = {
        "window_hours":  summary.get("window_hours"),
        "total_renders": summary.get("total_renders"),
        "success_rate":  round(float(summary.get("success_rate", 0.0)), 4),
        "tabs_tracked":  len(summary.get("by_tab", {})),
    }
    _print_kv(headline)


def _cmd_health_summary(args: argparse.Namespace) -> None:
    from engine.source_health import get_health_summary

    summary = get_health_summary()
    if args.json:
        _print_json(summary)
        return
    headline = {
        "window_hours":    summary.get("window_hours"),
        "total_pings":     summary.get("total_pings"),
        "sources_tracked": len(summary.get("by_source", {})),
        "current_outages": ", ".join(summary.get("current_outages", []) or []) or "(none)",
    }
    _print_kv(headline)


def _cmd_health_ping(args: argparse.Namespace) -> None:
    from engine.source_health import ping_all_sources

    pings = ping_all_sources()
    if args.json:
        _print_json(pings)
        return
    rows = [
        {
            "source":      p.source,
            "status":      p.status,
            "duration_ms": p.duration_ms,
            "error_msg":   (p.error_msg[:40] + "…") if len(p.error_msg) > 40 else p.error_msg,
        }
        for p in pings
    ]
    _print_table(rows, columns=["source", "status", "duration_ms", "error_msg"])


def _cmd_health_alerts_status(args: argparse.Namespace) -> None:
    from engine.source_health_alerts import get_recent_fire_count, load_config

    cfg = load_config()
    payload = {
        "enabled":                   bool(cfg.enabled),
        "red_threshold_minutes":     int(cfg.red_threshold_minutes),
        "yellow_threshold_minutes":  int(cfg.yellow_threshold_minutes),
        "cooldown_minutes":          int(cfg.cooldown_minutes),
        "recent_fires_last_hour":    int(get_recent_fire_count()),
    }
    if args.json:
        _print_json(payload)
        return
    _print_kv(payload)


def _cmd_health_alerts_enable(args: argparse.Namespace) -> None:
    from engine.source_health_alerts import load_config, save_config

    cfg = load_config()
    cfg.enabled = True
    ok = save_config(cfg)
    payload = {"enabled": True, "saved": bool(ok)}
    if args.json:
        _print_json(payload)
        return
    _print_kv(payload)


def _cmd_health_alerts_disable(args: argparse.Namespace) -> None:
    from engine.source_health_alerts import load_config, save_config

    cfg = load_config()
    cfg.enabled = False
    ok = save_config(cfg)
    payload = {"enabled": False, "saved": bool(ok)}
    if args.json:
        _print_json(payload)
        return
    _print_kv(payload)


def _cmd_health_alerts_run_once(args: argparse.Namespace) -> None:
    from engine.source_health_alerts import check_source_health_and_fire

    counts = check_source_health_and_fire()
    if args.json:
        _print_json(counts)
        return
    _print_kv(counts)


# ── users disable/enable (account kill-switch; R104 operator wrapper) ──────

def _cmd_users_disable(args: argparse.Namespace) -> None:
    from auth.audit import record_audit
    from auth.users import deactivate_user

    ok = deactivate_user(args.user_id)
    if ok:
        record_audit("disable_user", entity_type="user",
                     entity_id=args.user_id, user_id="")
    payload = {"user_id": args.user_id, "disabled": bool(ok),
               "note": "" if ok else "user not found"}
    _print_json(payload) if args.json else _print_kv(payload)


def _cmd_users_enable(args: argparse.Namespace) -> None:
    from auth.audit import record_audit
    from auth.users import reactivate_user

    ok = reactivate_user(args.user_id)
    if ok:
        record_audit("enable_user", entity_type="user",
                     entity_id=args.user_id, user_id="")
    payload = {"user_id": args.user_id, "enabled": bool(ok),
               "note": "" if ok else "user not found"}
    _print_json(payload) if args.json else _print_kv(payload)


def _cmd_perf_budgets_list(args: argparse.Namespace) -> None:
    """List every budget + current observed p95 + within-budget status.

    A read-only summary panel — joins the saved budgets against the
    latest perf summary so the operator can see at a glance which
    tabs are running hot.
    """
    from engine.perf_budgets import load_budgets, check_budgets

    budgets = load_budgets()
    breaches = {b.tab_module: b for b in check_budgets()}

    # Pull one perf summary per unique window so the observed p95 is
    # available even for tabs that are NOT in breach.
    from engine.perf_telemetry import get_perf_summary
    windows = sorted({int(b.window_hours) for b in budgets})
    summaries: dict[int, dict] = {}
    for w in windows:
        summaries[w] = get_perf_summary(window_hours=w) or {}

    payload = []
    for b in budgets:
        summary = summaries.get(int(b.window_hours), {})
        by_tab = summary.get("by_tab", {}) if isinstance(summary, dict) else {}
        stats = by_tab.get(b.tab_module) if isinstance(by_tab, dict) else None
        if isinstance(stats, dict):
            count = int(stats.get("count", 0) or 0)
            p95_s = int(stats.get("p95_ms", 0) or 0) / 1000.0
        else:
            count = 0
            p95_s = 0.0
        breach = breaches.get(b.tab_module)
        if breach is not None:
            status = breach.severity
        elif count == 0:
            status = "no-data"
        else:
            status = "ok"
        payload.append({
            "tab_module":   b.tab_module,
            "budget_p95":   round(float(b.max_p95_seconds), 2),
            "observed_p95": round(float(p95_s), 2),
            "samples":      count,
            "window_h":     int(b.window_hours),
            "status":       status,
        })

    if args.json:
        _print_json(payload)
        return
    _print_table(
        [
            {
                "tab_module":   p["tab_module"],
                "budget_p95":   f"{p['budget_p95']:.2f}s",
                "observed_p95": f"{p['observed_p95']:.2f}s",
                "samples":      str(p["samples"]),
                "window_h":     str(p["window_h"]),
                "status":       p["status"],
            }
            for p in payload
        ],
        columns=["tab_module", "budget_p95", "observed_p95", "samples", "window_h", "status"],
    )


def _cmd_perf_budgets_set(args: argparse.Namespace) -> None:
    """Set / replace the budget for one tab.

    Loads the current list, upserts the matching tab_module (matches
    on exact string equality), persists. If the tab is not in the
    current list, a new PerfBudget is appended.
    """
    from engine.perf_budgets import load_budgets, save_budgets, PerfBudget

    if args.max_p95 is None or float(args.max_p95) <= 0:
        raise RuntimeError("--max-p95 must be a positive number of seconds")

    budgets = load_budgets()
    new_p95 = float(args.max_p95)
    replaced = False
    for b in budgets:
        if b.tab_module == args.tab_module:
            b.max_p95_seconds = new_p95
            replaced = True
            break
    if not replaced:
        budgets.append(PerfBudget(
            tab_module=args.tab_module,
            max_p95_seconds=new_p95,
            max_mean_seconds=None,
            window_hours=24,
        ))

    ok = save_budgets(budgets)
    payload = {
        "tab_module":      args.tab_module,
        "max_p95_seconds": new_p95,
        "saved":           bool(ok),
        "action":          "replaced" if replaced else "created",
    }
    if args.json:
        _print_json(payload)
        return
    _print_kv(payload)


def _cmd_perf_budgets_reset(args: argparse.Namespace) -> None:
    """Wipe custom budgets and revert to the shipped defaults.

    Writes an empty list to the kv_state row; load_budgets treats that
    as "use defaults" on the next read.
    """
    from engine.perf_budgets import save_budgets, get_default_budgets

    ok = save_budgets([])
    defaults = get_default_budgets()
    payload = {
        "reset":         bool(ok),
        "default_count": len(defaults),
    }
    if args.json:
        _print_json(payload)
        return
    _print_kv(payload)


def _cmd_perf_budgets_check(args: argparse.Namespace) -> None:
    """Run check_and_alert NOW. Returns the count dict."""
    from engine.perf_budgets import check_and_alert

    counts = check_and_alert()
    if args.json:
        _print_json(counts)
        return
    _print_kv(counts)


# ─────────────────────────────────────────────────────────────────────────────
#  anomalies: time-series anomaly detection
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_anomalies_check(args: argparse.Namespace) -> None:
    """Run check_and_alert_anomalies NOW + print results.

    With --json the full result list is included; without --json a count
    summary plus a table of detected anomalies is printed.
    """
    from engine.anomaly_detect import (
        check_and_alert_anomalies,
        detect_all_anomalies,
    )

    # detect_all_anomalies gives us the full per-metric result rows for
    # the table render; check_and_alert_anomalies actually fires + stamps
    # cooldown. We run detect first so the table reflects what we'd
    # fire even when every metric is calm.
    user_id = getattr(args, "user_id", None)
    results = detect_all_anomalies(user_id=user_id)
    counts = check_and_alert_anomalies(user_id=user_id)

    if args.json:
        _print_json({
            "counts": counts,
            "results": [
                {
                    "metric_id":      r.metric_id,
                    "severity":       r.severity,
                    "observed_value": r.observed_value,
                    "baseline_mean":  r.baseline_mean,
                    "baseline_std":   r.baseline_std,
                    "z_score":        r.z_score,
                    "drift_pct":      r.drift_pct,
                    "message":        r.message,
                    "checked_at":     r.checked_at,
                }
                for r in results
            ],
        })
        return

    _print_kv(counts)
    if not results:
        return

    rows = [
        {
            "metric_id": r.metric_id,
            "severity":  r.severity,
            "z":         f"{r.z_score:+.2f}",
            "drift_pct": f"{r.drift_pct:+.2f}%",
            "observed":  f"{r.observed_value:.4g}",
            "baseline":  f"{r.baseline_mean:.4g}",
        }
        for r in results
    ]
    _print_table(
        rows,
        columns=["metric_id", "severity", "z", "drift_pct", "observed", "baseline"],
    )


def _cmd_anomalies_configs(args: argparse.Namespace) -> None:
    """Show the per-metric configs for the (current or named) user."""
    from engine.anomaly_detect import get_anomaly_configs

    configs = get_anomaly_configs(user_id=getattr(args, "user_id", None))
    payload = [
        {
            "metric_id":     c.metric_id,
            "enabled":       bool(c.enabled),
            "method":        c.method,
            "lookback_days": int(c.lookback_days),
            "z_threshold":   float(c.z_threshold),
            "min_samples":   int(c.min_samples),
        }
        for c in configs
    ]
    if args.json:
        _print_json(payload)
        return
    _print_table(
        [
            {
                "metric_id":     p["metric_id"],
                "enabled":       "yes" if p["enabled"] else "no",
                "method":        p["method"],
                "lookback_days": str(p["lookback_days"]),
                "z_threshold":   f"{p['z_threshold']:.2f}",
                "min_samples":   str(p["min_samples"]),
            }
            for p in payload
        ],
        columns=["metric_id", "enabled", "method", "lookback_days",
                 "z_threshold", "min_samples"],
    )


def _anomalies_upsert_config(metric_id: str, user_id, mutator) -> dict:
    """Helper: load configs, mutate the matching row, save.

    ``mutator`` is a callable that takes the matching :class:`AnomalyConfig`
    and mutates it in place. New rows are NOT auto-created — the
    ``enable``/``disable``/``set`` CLI commands only operate on metrics
    that already have a config. This keeps the registry tied to the
    built-in defaults; adding a custom metric requires editing the
    module.
    """
    from engine.anomaly_detect import get_anomaly_configs, save_anomaly_configs

    configs = get_anomaly_configs(user_id=user_id)
    found = False
    for c in configs:
        if c.metric_id == metric_id:
            mutator(c)
            found = True
            break
    if not found:
        return {"metric_id": metric_id, "saved": False, "error": "unknown metric_id"}
    ok = save_anomaly_configs(configs, user_id=user_id)
    return {"metric_id": metric_id, "saved": bool(ok)}


def _cmd_anomalies_enable(args: argparse.Namespace) -> None:
    """Flip the enabled flag ON for one metric."""
    payload = _anomalies_upsert_config(
        args.metric_id, getattr(args, "user_id", None),
        lambda c: setattr(c, "enabled", True),
    )
    payload["action"] = "enable"
    if args.json:
        _print_json(payload)
        return
    _print_kv(payload)


def _cmd_anomalies_disable(args: argparse.Namespace) -> None:
    """Flip the enabled flag OFF for one metric."""
    payload = _anomalies_upsert_config(
        args.metric_id, getattr(args, "user_id", None),
        lambda c: setattr(c, "enabled", False),
    )
    payload["action"] = "disable"
    if args.json:
        _print_json(payload)
        return
    _print_kv(payload)


def _cmd_anomalies_set(args: argparse.Namespace) -> None:
    """Update z-threshold / lookback / method on one metric."""
    z = getattr(args, "z_threshold", None)
    lb = getattr(args, "lookback_days", None)
    method = getattr(args, "method", None)

    def _apply(c) -> None:
        if z is not None:
            c.z_threshold = float(z)
        if lb is not None:
            c.lookback_days = int(lb)
        if method is not None:
            c.method = str(method)

    payload = _anomalies_upsert_config(
        args.metric_id, getattr(args, "user_id", None), _apply,
    )
    payload["action"] = "set"
    if z is not None:
        payload["z_threshold"] = float(z)
    if lb is not None:
        payload["lookback_days"] = int(lb)
    if method is not None:
        payload["method"] = str(method)
    if args.json:
        _print_json(payload)
        return
    _print_kv(payload)


def _cmd_users_list(args: argparse.Namespace) -> None:
    from auth.users import list_users

    users = list_users()
    if args.json:
        _print_json(users)
        return
    rows = [
        {
            "user_id":       u.user_id[:10],
            "username":      u.username,
            "role":          u.role,
            "active":        "yes" if u.is_active else "NO",
            "created_at":    u.created_at,
            "last_login_at": u.last_login_at or "(never)",
        }
        for u in users
    ]
    _print_table(rows, columns=["user_id", "username", "role", "active", "created_at", "last_login_at"])


def _cmd_users_create(args: argparse.Namespace) -> None:
    from auth.users import signup

    user = signup(args.username, args.password)
    if user is None:
        # signup() never raises — None means validation/duplicate
        # failed. Convert to a non-zero exit so a calling script can
        # detect the failure.
        raise RuntimeError(
            f"signup failed for username={args.username!r} — "
            "duplicate, weak password, or invalid format"
        )
    if args.json:
        _print_json(user)
    else:
        # Don't echo the password back; show the new user's metadata.
        _print_kv({
            "user_id":    user.user_id,
            "username":   user.username,
            "role":       user.role,
            "created_at": user.created_at,
        })


def _cmd_tokens_list(args: argparse.Namespace) -> None:
    from auth.tokens import list_tokens

    tokens = list_tokens(args.user_id)
    if args.json:
        _print_json(tokens)
        return
    rows = [
        {
            "token_id":     t.token_id[:10],
            "label":        t.label,
            "prefix":       t.token_prefix,
            "created_at":   t.created_at,
            "last_used_at": t.last_used_at or "(never)",
            "expires_at":   t.expires_at or "(never)",
            "revoked":      "Y" if t.revoked else "N",
        }
        for t in tokens
    ]
    _print_table(rows, columns=["token_id", "label", "prefix", "created_at", "last_used_at", "expires_at", "revoked"])


def _cmd_tokens_create(args: argparse.Namespace) -> None:
    from auth.tokens import create_token

    result = create_token(
        args.user_id, args.label,
        expires_in_days=getattr(args, "expires_in_days", None),
    )
    if result is None:
        raise RuntimeError(
            f"create_token failed for user_id={args.user_id!r} — "
            "empty user_id, empty label, or DB error"
        )
    meta, raw_token = result
    if args.json:
        # The raw token MUST appear exactly once in the output stream;
        # callers see it via stdout and never again. ``meta`` carries the
        # public metadata (no hash / salt by dataclass design).
        _print_json({"token": raw_token, "meta": meta})
    else:
        # Print the raw token EXACTLY ONCE on its own line, prefixed so
        # an operator copy-pasting from terminal output can spot it.
        print(f"token: {raw_token}")
        _print_kv({
            "token_id":   meta.token_id,
            "label":      meta.label,
            "prefix":     meta.token_prefix,
            "created_at": meta.created_at,
            "expires_at": meta.expires_at or "(never)",
        })


def _cmd_tokens_revoke(args: argparse.Namespace) -> None:
    from auth.tokens import revoke_token

    ok = revoke_token(args.token_id, user_id=args.user_id)
    payload = {"token_id": args.token_id, "revoked": bool(ok)}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_export(args: argparse.Namespace) -> None:
    from utils.bulk_export import build_export

    out: Optional[Path] = None
    if args.output:
        out = Path(args.output)
    result = build_export(output_path=out)
    if result is None:
        raise RuntimeError("build_export returned None — see logs for details")
    payload = {"output_path": str(result), "size_bytes": int(result.stat().st_size) if result.exists() else 0}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


# ─────────────────────────────────────────────────────────────────────────────
#  audit — read-only export of the audit log for SIEM ingestion
#
#  The Streamlit UI surfaces audit_events as a parsed table; the API
#  emits the JSON envelope ``{items: [...], count: N}``. Neither shape
#  is what Splunk / Vector / Loki want — those tools speak JSONL (one
#  JSON object per line, \n-delimited). This subcommand bridges that
#  gap: it pipes audit rows through ``utils.audit_export`` and either
#  streams them to stdout (default) or writes them to ``--out PATH``.
#
#  Output contract:
#    * Default: full JSONL on stdout, progress line on stderr.
#    * --out PATH: streams via ``export_audit_to_stream`` (memory-
#      efficient for very large pulls), progress on stderr, prints
#      the path on stdout so a calling script can pipe it.
#
#  Exit codes:
#    * 0 on success (including the empty-result case)
#    * 1 on argument / ISO-parsing errors (handled by main()'s
#      try / except wrapper)
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_audit_export(args: argparse.Namespace) -> None:
    """Export audit rows to JSONL — stdout or --out FILE.

    Stderr carries a ``exported N rows in M ms`` progress line so a
    shell pipe consumer can capture the JSONL on stdout untouched
    while still seeing the row count for monitoring.

    Argument validation:
      * ``--since`` / ``--until`` MUST parse via ``datetime.fromisoformat``
        (the canonical ISO-8601 subset Python supports). A malformed
        value raises ``RuntimeError`` here, which ``main()`` converts
        to exit 1 with the message on stderr — same contract as every
        other CLI handler.
      * ``--limit`` MUST be a positive integer. Argparse already
        enforces ``type=int``; we additionally floor at 1 so a typo'd
        ``--limit 0`` doesn't silently produce an empty export.
    """
    import time

    from utils.audit_export import (
        export_audit_to_jsonl,
        export_audit_to_stream,
    )

    # Validate ISO-8601 inputs up front so a bad value fails BEFORE
    # we touch the DB. fromisoformat accepts the same range query_audit
    # records (UTC ISO strings via datetime.isoformat()).
    for name, raw in (("since", args.since), ("until", args.until)):
        if raw is None:
            continue
        try:
            datetime.fromisoformat(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"invalid --{name} value {raw!r}: must be ISO-8601 "
                f"(e.g. 2026-05-23T00:00:00+00:00) — {exc}"
            ) from exc

    limit = max(1, int(args.limit))
    filters = dict(
        user_id=args.user_id,
        action=args.action,
        since=args.since,
        until=args.until,
        limit=limit,
    )

    started_ns = time.perf_counter_ns()

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Streaming path — write directly to the file in batches so a
        # 100k-row backfill doesn't materialise as one giant string.
        with out_path.open("wb") as f:
            row_count = export_audit_to_stream(f, **filters)
        elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000.0
        # Progress on STDERR so the stdout channel stays clean for
        # the path (which a calling shell may want to capture).
        print(
            f"exported {row_count} rows in {elapsed_ms:.0f} ms to {out_path}",
            file=sys.stderr,
        )
        # Path on stdout — matches the pattern used by ``rules export
        # --out`` and ``export --output``.
        print(str(out_path))
        return

    # No --out → stream to stdout. We use the in-memory variant here
    # because stdout is by definition a text stream and we want the
    # progress line to print AFTER the JSONL body lands so the user
    # sees the headline at the bottom (matches the convention of
    # `wc -l file | xargs echo`).
    body = export_audit_to_jsonl(**filters)
    # sys.stdout.write avoids the trailing newline print() would add
    # — rows_to_jsonl already terminates the last line. Decoding to
    # str is safe: the bytes are UTF-8 by construction.
    sys.stdout.write(body.decode("utf-8"))
    # Count rows by counting newlines (every row ends in \n, including
    # the last). Empty result → 0 rows.
    row_count = body.count(b"\n") if body else 0
    elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000.0
    print(
        f"exported {row_count} rows in {elapsed_ms:.0f} ms",
        file=sys.stderr,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  mfa — TOTP second factor (commit 1ac149b)
#
#  The interesting handler is ``mfa enable``: it generates a fresh secret
#  + provisioning URI, prints BOTH to stdout (the operator scans the QR
#  built from the URI, or pastes the raw secret into an authenticator
#  app that doesn't ship a scanner), then flips the DB row.
#
#  The raw secret MUST land on stdout only — never stderr, never the
#  loguru sink (we explicitly do NOT log it from this CLI). Tests assert
#  it shows up exactly once.
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_mfa_enable(args: argparse.Namespace) -> None:
    from auth.mfa import enable_mfa, generate_secret, provisioning_uri
    from auth.users import get_user

    # Resolve the username for the provisioning URI label. Falls back to
    # ``user_id`` when the user has no username (legacy / synthetic ids).
    user = get_user(args.user_id)
    account = user.username if user is not None else args.user_id

    # Use an operator-supplied secret when given (so --code can do a real
    # proof-of-possession round-trip); otherwise generate a fresh one. A
    # generated secret has no matching code the operator could supply in one
    # shot, so the no-PoP provisioning path stays the default.
    secret = getattr(args, "secret", None) or generate_secret()
    uri = provisioning_uri(secret, account=account)
    # v21 signature change: enable_mfa returns (ok, recovery_codes).
    # The recovery codes are surfaced to the operator EXACTLY ONCE
    # on stdout — never logged, never re-derivable from the DB. The
    # raw secret is similarly shown once. ``code`` (when supplied) makes
    # enable_mfa enforce proof-of-possession and refuse on a mismatch.
    ok, recovery_codes = enable_mfa(
        args.user_id, secret, code=getattr(args, "code", None),
    )
    if not ok:
        raise RuntimeError(
            f"enable_mfa failed for user_id={args.user_id!r} — unknown user, "
            "DB error, or (with --code) the code did not match the secret"
        )

    payload = {
        "user_id": args.user_id,
        "secret": secret,
        "provisioning_uri": uri,
        "enabled": True,
        "recovery_codes": recovery_codes or [],
    }
    if args.json:
        _print_json(payload)
    else:
        # Print every piece explicitly so an operator copy-pasting from
        # the terminal can spot each on its own line. The raw secret is
        # what the user types into an authenticator app that can't scan
        # a QR; the URI is the canonical otpauth:// form for QR-capable
        # apps. Recovery codes (when present — auto-mint may fail
        # gracefully with None) land in a labelled block at the end so
        # they don't get visually lost between the secret + URI lines.
        print(f"secret: {secret}")
        print(f"provisioning_uri: {uri}")
        _print_kv({"user_id": args.user_id, "enabled": True})
        if recovery_codes:
            print("recovery_codes (save these — shown once):")
            for code in recovery_codes:
                print(f"  {code}")
        else:
            print(
                "recovery_codes: (auto-mint failed; run "
                "'mfa regenerate-codes' to retry)"
            )


def _cmd_mfa_disable(args: argparse.Namespace) -> None:
    from auth.mfa import disable_mfa

    ok = disable_mfa(args.user_id)
    if not ok:
        raise RuntimeError(
            f"disable_mfa failed for user_id={args.user_id!r} — "
            "unknown user or DB error"
        )
    payload = {"user_id": args.user_id, "enabled": False}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_mfa_status(args: argparse.Namespace) -> None:
    from auth.mfa import is_mfa_enabled

    enabled = is_mfa_enabled(args.user_id)
    payload = {"user_id": args.user_id, "enabled": bool(enabled)}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_mfa_recovery_codes(args: argparse.Namespace) -> None:
    """Report how many UNUSED recovery codes the user currently has.

    Does NOT print the codes themselves — they are unrecoverable
    after the initial mint. Use ``mfa regenerate-codes`` to issue a
    fresh batch (and surface it once).
    """
    from auth.mfa import count_unused_recovery_codes

    n = count_unused_recovery_codes(args.user_id)
    payload = {"user_id": args.user_id, "unused_count": int(n)}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_mfa_regenerate_codes(args: argparse.Namespace) -> None:
    """Wipe the current recovery-code batch and issue a fresh one.

    Prints the plaintext codes EXACTLY ONCE on stdout. After this
    handler exits the codes are unrecoverable (the DB only carries
    the per-code pbkdf2 hash).
    """
    from auth.mfa import regenerate_recovery_codes

    codes = regenerate_recovery_codes(args.user_id)
    if not codes:
        raise RuntimeError(
            f"regenerate_recovery_codes failed for "
            f"user_id={args.user_id!r} — unknown user or DB error"
        )
    payload = {"user_id": args.user_id, "recovery_codes": codes}
    if args.json:
        _print_json(payload)
    else:
        _print_kv({"user_id": args.user_id, "count": len(codes)})
        print("recovery_codes (save these — shown once):")
        for code in codes:
            print(f"  {code}")


# ─────────────────────────────────────────────────────────────────────────────
#  invite — admin-issued user invitations (v21)
#
#  The interesting handler is ``invite create``: it mints a token and
#  prints it EXACTLY ONCE to stdout (same security contract as
#  ``mfa enable`` and ``tokens create`` — the operator copy-pastes from
#  terminal output and the token is never re-derivable from the DB).
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_invite_create(args: argparse.Namespace) -> None:
    from auth.invitations import create_invitation

    invitation = create_invitation(
        args.invited_by,
        email=args.email,
        role=args.role,
        expires_in_days=int(args.expires_days),
    )
    if invitation is None:
        raise RuntimeError(
            f"create_invitation failed for invited_by={args.invited_by!r} — "
            "unknown user, invalid role, or DB error"
        )
    if args.json:
        # The raw token MUST appear exactly once in the output. Tests
        # pin this — duplicate appearances would let a careless copy-
        # paste leak the secret twice.
        _print_json({
            "invite_id":         invitation.invite_id,
            "invite_token":      invitation.invite_token,
            "email":             invitation.email,
            "role":              invitation.role,
            "expires_at":        invitation.expires_at,
            "invited_by_user_id": invitation.invited_by_user_id,
        })
    else:
        # Token on its own line, prefixed for terminal copy-paste.
        print(f"invite_token: {invitation.invite_token}")
        _print_kv({
            "invite_id":  invitation.invite_id,
            "email":      invitation.email or "(any)",
            "role":       invitation.role,
            "expires_at": invitation.expires_at,
        })


def _cmd_invite_list(args: argparse.Namespace) -> None:
    from auth.invitations import list_invitations

    invitations = list_invitations(
        invited_by_user_id=getattr(args, "invited_by", None),
        include_consumed=bool(args.include_consumed),
    )
    if args.json:
        _print_json(invitations)
        return
    rows = [
        {
            "invite_id":   inv.invite_id[:10],
            "email":       inv.email or "(any)",
            "role":        inv.role,
            "expires_at":  inv.expires_at[:19],
            "consumed":    "Y" if inv.consumed_at else "N",
            "invited_by":  inv.invited_by_user_id[:10],
        }
        for inv in invitations
    ]
    _print_table(
        rows,
        columns=["invite_id", "email", "role", "expires_at", "consumed", "invited_by"],
    )


def _cmd_invite_revoke(args: argparse.Namespace) -> None:
    from auth.invitations import revoke_invitation

    ok = revoke_invitation(args.invite_id)
    payload = {"invite_id": args.invite_id, "revoked": bool(ok)}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


# ─────────────────────────────────────────────────────────────────────────────
#  filters — per-user saved filter presets (commit e471855)
#
#  Only list + delete here. Save is the UI's job — the CLI would need a
#  way to encode the per-scope payload vocabulary on the command line,
#  and that's a follow-up if anyone asks.
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_filters_list(args: argparse.Namespace) -> None:
    from state.user_filters import load_presets

    presets = load_presets(user_id=args.user_id, scope=args.scope)
    if args.json:
        _print_json(presets)
        return
    rows = [
        {
            "name":     p.name,
            "scope":    p.scope,
            "keys":     ",".join(sorted(p.payload.keys())) if isinstance(p.payload, dict) else "",
        }
        for p in presets
    ]
    _print_table(rows, columns=["name", "scope", "keys"])


def _cmd_filters_delete(args: argparse.Namespace) -> None:
    from state.user_filters import delete_preset

    ok = delete_preset(args.name, args.scope, user_id=args.user_id)
    payload = {"name": args.name, "scope": args.scope, "deleted": bool(ok)}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


# ─────────────────────────────────────────────────────────────────────────────
#  incidents — alert correlation read-side (commit 665487d)
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_incidents_list(args: argparse.Namespace) -> None:
    from engine.alert_correlator import get_recent_incidents

    incidents = get_recent_incidents(window_days=int(args.window))
    if args.json:
        _print_json(incidents)
        return
    rows = [
        {
            "incident_id":  inc.incident_id[:10],
            "started_at":   inc.started_at,
            "severity_max": inc.severity_max,
            "alerts":       inc.alert_count,
            "dominant":     inc.dominant_alert_type,
        }
        for inc in incidents
    ]
    _print_table(rows, columns=["incident_id", "started_at", "severity_max", "alerts", "dominant"])


def _cmd_incidents_stats(args: argparse.Namespace) -> None:
    from engine.alert_correlator import get_incident_summary

    summary = get_incident_summary(window_days=int(args.window))
    if args.json:
        _print_json(summary)
        return
    # The breakdown dict is nested — show the headline numbers and the
    # number of distinct dominant types as a cheap one-liner. Full
    # breakdown is JSON-only (matches ``telemetry usage`` pattern).
    headline = {
        "window_days":              int(args.window),
        "n_incidents":              summary.get("n_incidents", 0),
        "n_total_alerts":           summary.get("n_total_alerts", 0),
        "avg_alerts_per_incident":  round(float(summary.get("avg_alerts_per_incident", 0.0)), 4),
        "largest_incident_size":    summary.get("largest_incident_size", 0),
        "n_dominant_types":         len(summary.get("breakdown_by_dominant_type", {})),
    }
    _print_kv(headline)


# ─────────────────────────────────────────────────────────────────────────────
#  settings — per-user preferences (commit 9f79568)
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_settings_show(args: argparse.Namespace) -> None:
    from auth.settings import get_settings

    settings = get_settings(args.user_id)
    if args.json:
        _print_json(settings)
        return
    _print_kv({
        "user_id":                    settings.user_id,
        "timezone":                   settings.timezone,
        "theme":                      settings.theme,
        "default_report_window_days": settings.default_report_window_days,
        "default_alert_severity":     settings.default_alert_severity,
        "extras":                     settings.extras or "(none)",
    })


# ─────────────────────────────────────────────────────────────────────────────
#  prefs — per-user notification preferences (auth.notification_prefs)
#
#  Per-operator overlay on top of the existing per-rule + per-channel
#  routing. Three subcommands — show / set / reset. ``--user-id`` is
#  required on every subcommand (alice cannot edit bob's prefs from
#  the CLI any more than from the UI).
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_prefs_show(args: argparse.Namespace) -> None:
    from auth.notification_prefs import get_prefs

    prefs = get_prefs(user_id=args.user_id)
    payload = {
        "user_id":              prefs.user_id,
        "enabled":              prefs.enabled,
        "min_severity":         prefs.min_severity,
        "alert_type_filter":    prefs.alert_type_filter,
        "severity_channel_map": prefs.severity_channel_map,
        "quiet_during_hours":   list(prefs.quiet_during_hours)
                                if prefs.quiet_during_hours is not None
                                else None,
    }
    if args.json:
        _print_json(payload)
    else:
        _print_kv({
            "user_id":            prefs.user_id,
            "enabled":            prefs.enabled,
            "min_severity":       prefs.min_severity,
            "alert_type_filter":  ",".join(prefs.alert_type_filter) or "(any)",
            "quiet_during_hours": (
                f"{prefs.quiet_during_hours[0]:02d}:00 → "
                f"{prefs.quiet_during_hours[1]:02d}:00 UTC"
                if prefs.quiet_during_hours is not None
                else "(none)"
            ),
            "severity_channels":  (
                "; ".join(
                    f"{sev}:{','.join(chs) if chs else '(none)'}"
                    for sev, chs in sorted(prefs.severity_channel_map.items())
                )
                if prefs.severity_channel_map else "(no per-severity map)"
            ),
        })


def _cmd_prefs_set(args: argparse.Namespace) -> None:
    from auth.notification_prefs import update_pref

    updates: dict[str, Any] = {}
    if args.min_severity is not None:
        updates["min_severity"] = args.min_severity
    if args.enabled is not None:
        # argparse delivers the raw string ("true"/"false"); coerce here
        # so the prefs module only sees a real bool.
        enabled_str = str(args.enabled).strip().lower()
        if enabled_str not in ("true", "false", "1", "0"):
            raise RuntimeError(
                "prefs set --enabled must be 'true' or 'false'"
            )
        updates["enabled"] = enabled_str in ("true", "1")
    if args.alert_types is not None:
        # Comma-separated list — split + strip + drop blanks. An empty
        # final list ("--alert-types ''") explicitly clears the filter.
        updates["alert_type_filter"] = [
            s.strip() for s in args.alert_types.split(",") if s.strip()
        ]
    # Quiet hours: both halves must be supplied together. The CLI does
    # NOT support setting only one half — that would silently leave the
    # window half-configured.
    if args.quiet_start is not None or args.quiet_end is not None:
        if args.quiet_start is None or args.quiet_end is None:
            raise RuntimeError(
                "prefs set requires both --quiet-start AND --quiet-end "
                "(or neither)"
            )
        updates["quiet_during_hours"] = (
            int(args.quiet_start), int(args.quiet_end),
        )
    if args.clear_quiet_hours:
        # Explicit clear. Takes precedence over any --quiet-* values
        # passed in the same invocation — "clear and set" makes no sense.
        updates["quiet_during_hours"] = None

    if not updates:
        raise RuntimeError(
            "prefs set requires at least one of --enabled / --min-severity "
            "/ --alert-types / --quiet-start+--quiet-end / --clear-quiet-hours"
        )

    ok = update_pref(user_id=args.user_id, **updates)
    payload = {
        "user_id": args.user_id,
        "applied": list(updates.keys()),
        "saved":   bool(ok),
    }
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_prefs_reset(args: argparse.Namespace) -> None:
    from auth.notification_prefs import reset_prefs

    ok = reset_prefs(user_id=args.user_id)
    payload = {"user_id": args.user_id, "reset": bool(ok)}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


# ─────────────────────────────────────────────────────────────────────────────
#  silences — bounded alert silencing for planned downtime (schema v22)
#
#  Mirrors the API + UI surface: list / create / delete. Every
#  subcommand is per-user scoped via the ``--user-id`` flag — alice
#  cannot list, mutate, or delete bob's silences. The CLI does not
#  expose a separate ``--created-by`` flag because the operator
#  running a shell is presumed to be acting on their own behalf;
#  ``created_by_user_id`` is stamped equal to ``--user-id``.
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_silences_list(args: argparse.Namespace) -> None:
    """List a user's silences. Defaults to active-only; pass
    ``--include-expired`` to surface the audit-retention tail."""
    from engine.alert_silences import list_silences

    silences = list_silences(
        user_id=args.user_id,
        include_expired=bool(getattr(args, "include_expired", False)),
    )
    if args.json:
        _print_json(silences)
        return
    rows = [
        {
            "silence_id":  s.silence_id[:10],
            "rule_id":     s.rule_id or "(all)",
            "ticker":      s.ticker or "(all)",
            "severity":    s.severity or "(all)",
            "starts_at":   s.starts_at,
            "expires_at":  s.expires_at,
            "reason":      (s.reason[:40] + "…")
                           if (s.reason and len(s.reason) > 40) else (s.reason or ""),
        }
        for s in silences
    ]
    _print_table(
        rows,
        columns=["silence_id", "rule_id", "ticker", "severity",
                 "starts_at", "expires_at", "reason"],
    )


def _cmd_silences_create(args: argparse.Namespace) -> None:
    """Create a silence with the supplied filters + duration. The
    ``--rule-id`` / ``--ticker`` / ``--severity`` flags are all
    optional — omitted ones become NULL on the row (matches anything
    for that column)."""
    from engine.alert_silences import create_silence

    duration = int(args.duration_minutes)
    silence = create_silence(
        user_id=args.user_id,
        rule_id=args.rule_id,
        ticker=args.ticker,
        severity=args.severity,
        reason=args.reason,
        duration_minutes=duration,
        # The CLI has no session — the operator running it is acting
        # on their own behalf, so created_by_user_id matches the
        # silence owner.
        created_by_user_id=args.user_id,
    )
    if silence is None:
        raise RuntimeError(
            f"create_silence failed for user_id={args.user_id!r} "
            "— check logs"
        )
    if args.json:
        _print_json(silence)
    else:
        _print_kv({
            "silence_id":  silence.silence_id,
            "user_id":     silence.user_id,
            "rule_id":     silence.rule_id or "(all)",
            "ticker":      silence.ticker or "(all)",
            "severity":    silence.severity or "(all)",
            "starts_at":   silence.starts_at,
            "expires_at":  silence.expires_at,
            "reason":      silence.reason or "",
        })


def _cmd_silences_delete(args: argparse.Namespace) -> None:
    """Cancel a silence early. Per-user scoped — alice cannot delete
    bob's silence."""
    from engine.alert_silences import delete_silence

    ok = delete_silence(args.silence_id, user_id=args.user_id)
    payload = {"silence_id": args.silence_id, "deleted": bool(ok)}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


# ─────────────────────────────────────────────────────────────────────────────
#  annotations — per-alert operator commentary threads (schema v23)
#
#  Mirrors the API + UI surface: list / add / delete. Every subcommand
#  is per-user scoped via the ``--user-id`` flag (the alert owner —
#  alice cannot list, mutate, or delete bob's alert annotations). The
#  CLI does not expose a separate ``--author`` flag because the
#  operator running a shell is presumed to be acting on their own
#  behalf; ``author_user_id`` is stamped equal to ``--user-id``.
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_annotations_list(args: argparse.Namespace) -> None:
    """List the annotation thread for one alert in created_at ASC
    order. Empty thread prints "(no rows)" not a crash."""
    from engine.alert_annotations import list_annotations

    annotations = list_annotations(args.alert_id, user_id=args.user_id)
    if args.json:
        _print_json(annotations)
        return
    rows = [
        {
            "annotation_id":  a.annotation_id[:10],
            "author":         (a.author_user_id or "")[:10],
            "created_at":     a.created_at,
            "edited_at":      a.edited_at or "",
            "body":           (a.body[:60] + "…")
                              if len(a.body) > 60 else a.body,
        }
        for a in annotations
    ]
    _print_table(
        rows,
        columns=["annotation_id", "author", "created_at",
                 "edited_at", "body"],
    )


def _cmd_annotations_add(args: argparse.Namespace) -> None:
    """Add one annotation to an alert. The body is silently
    truncated at 4000 chars by the engine layer — that contract is
    documented in ``engine.alert_annotations``."""
    from engine.alert_annotations import add_annotation

    saved = add_annotation(
        args.alert_id,
        args.body,
        user_id=args.user_id,
        # The CLI has no session — the operator running it is acting
        # on their own behalf, so author_user_id matches the alert
        # owner. The multi-user share case is exercised via the API /
        # UI surfaces.
        author_user_id=args.user_id,
    )
    if saved is None:
        raise RuntimeError(
            f"add_annotation failed for alert_id={args.alert_id!r} "
            "user_id={args.user_id!r} — empty body or DB error; "
            "see logs"
        )
    if args.json:
        _print_json(saved)
    else:
        _print_kv({
            "annotation_id":   saved.annotation_id,
            "alert_id":        saved.alert_id,
            "user_id":         saved.user_id,
            "author_user_id":  saved.author_user_id,
            "created_at":      saved.created_at,
            "body":            saved.body,
        })


def _cmd_annotations_delete(args: argparse.Namespace) -> None:
    """Delete one annotation. Per-user + per-author scoped — the row
    must belong to the caller AND the caller must be its author."""
    from engine.alert_annotations import delete_annotation

    ok = delete_annotation(
        args.annotation_id,
        user_id=args.user_id,
        author_user_id=args.user_id,
    )
    payload = {
        "annotation_id": args.annotation_id,
        "deleted":       bool(ok),
    }
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


# ─────────────────────────────────────────────────────────────────────────────
#  schedules — cron-driven auto-generated reports (commit / schema v20)
#
#  Mirrors the API + UI surface: list / create / delete / enable /
#  disable / run-once. Every subcommand is per-user scoped via the
#  ``--user-id`` flag — the CLI deliberately requires it explicitly
#  rather than reading session state because there IS no session state
#  in a shell process.
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_schedules_list(args: argparse.Namespace) -> None:
    from engine.report_scheduler import load_schedules

    schedules = load_schedules(user_id=args.user_id)
    if args.json:
        _print_json(schedules)
        return
    rows = [
        {
            "schedule_id":      s.schedule_id[:10],
            "name":             s.name,
            "cron_expr":        s.cron_expr,
            "enabled":          "Y" if s.enabled else "N",
            "next_run_at":      s.next_run_at or "(none)",
            "last_run_status":  s.last_run_status or "(never)",
        }
        for s in schedules
    ]
    _print_table(
        rows,
        columns=["schedule_id", "name", "cron_expr", "enabled",
                 "next_run_at", "last_run_status"],
    )


def _cmd_schedules_create(args: argparse.Namespace) -> None:
    from engine.report_scheduler import (
        ReportSchedule,
        new_schedule_id,
        save_schedule,
        validate_cron_expr,
    )

    ok, err = validate_cron_expr(args.cron)
    if not ok:
        raise RuntimeError(f"invalid cron expression {args.cron!r}: {err}")

    sched = ReportSchedule(
        schedule_id=new_schedule_id(),
        user_id=args.user_id,
        name=args.name,
        cron_expr=args.cron,
        enabled=True,
    )
    if not save_schedule(sched):
        raise RuntimeError(
            f"save_schedule failed for name={args.name!r} cron={args.cron!r} "
            "— check logs"
        )
    if args.json:
        _print_json(sched)
    else:
        _print_kv({
            "schedule_id": sched.schedule_id,
            "name":        sched.name,
            "cron_expr":   sched.cron_expr,
            "enabled":     "Y",
        })


def _cmd_schedules_delete(args: argparse.Namespace) -> None:
    from engine.report_scheduler import delete_schedule

    ok = delete_schedule(args.schedule_id, user_id=args.user_id)
    payload = {"schedule_id": args.schedule_id, "deleted": bool(ok)}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _toggle_schedule(schedule_id: str, user_id: str, enabled: bool) -> bool:
    """Shared helper for enable/disable — loads the row, flips the bit,
    saves it back. Returns True on success, False when the schedule
    doesn't exist in the caller's scope OR the save itself failed.

    Implemented as a separate helper because both ``enable`` and
    ``disable`` need the same load → mutate → save round-trip with the
    same per-user scope check; copying the logic into both handlers
    would invite drift the next time the save path changes.
    """
    from engine.report_scheduler import get_schedule, save_schedule

    sched = get_schedule(schedule_id, user_id=user_id)
    if sched is None:
        return False
    sched.enabled = enabled
    return save_schedule(sched)


def _cmd_schedules_enable(args: argparse.Namespace) -> None:
    ok = _toggle_schedule(args.schedule_id, args.user_id, enabled=True)
    if not ok:
        raise RuntimeError(
            f"enable failed for schedule_id={args.schedule_id!r} — "
            "unknown id or save failed"
        )
    payload = {"schedule_id": args.schedule_id, "enabled": True}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_schedules_disable(args: argparse.Namespace) -> None:
    ok = _toggle_schedule(args.schedule_id, args.user_id, enabled=False)
    if not ok:
        raise RuntimeError(
            f"disable failed for schedule_id={args.schedule_id!r} — "
            "unknown id or save failed"
        )
    payload = {"schedule_id": args.schedule_id, "enabled": False}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_schedules_run_once(args: argparse.Namespace) -> None:
    """Trigger an immediate manual run of one schedule.

    The execution path mirrors ``worker.scheduler.run_report_scheduler_job``
    for a single schedule: build the data bundle, call
    ``run_daily_briefing_job``, update the schedule's bookkeeping
    columns. The schedule's enabled state is IGNORED — operators
    explicitly asking for "run this NOW" should not be blocked by the
    enabled flag (use ``schedules enable`` separately if you want the
    cron to keep firing).
    """
    from engine.report_scheduler import (
        compute_next_run_at,
        get_schedule,
        update_run_state,
    )

    sched = get_schedule(args.schedule_id, user_id=args.user_id)
    if sched is None:
        raise RuntimeError(
            f"unknown schedule_id={args.schedule_id!r} for user_id={args.user_id!r}"
        )

    from worker.scheduler import load_data_bundle, run_daily_briefing_job

    bundle = load_data_bundle()
    result = run_daily_briefing_job(bundle, push_to_channels=False)

    # Advance next_run_at the same way the worker would, so a
    # successful run-once doesn't leave the schedule due for an
    # immediate re-fire on the next worker tick.
    try:
        next_iso = compute_next_run_at(sched.cron_expr).isoformat()
    except Exception:
        next_iso = None

    if result.success:
        update_run_state(
            sched.schedule_id, status="ok", message="", next_run_at=next_iso,
        )
        payload = {
            "schedule_id": sched.schedule_id,
            "success":     True,
            "report_id":   result.report_id,
            "next_run_at": next_iso,
        }
    else:
        update_run_state(
            sched.schedule_id,
            status="error",
            message=(result.error_msg or "unknown error")[:500],
            next_run_at=next_iso,
        )
        payload = {
            "schedule_id": sched.schedule_id,
            "success":     False,
            "error_msg":   result.error_msg,
            "next_run_at": next_iso,
        }
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


# ─────────────────────────────────────────────────────────────────────────────
#  rules — config-as-code for alert rules (commit / feat/rules-as-code)
#
#  Three subcommands: export (rules → YAML on stdout / file), import
#  (YAML → rules, replacing the user's set), diff (YAML vs current,
#  unified-diff style). Mirrors the UI's Export/Import expander but
#  available from a shell so the operator can version the YAML in git
#  and ship rule sets to colleagues without copy-pasting.
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_rules_export(args: argparse.Namespace) -> None:
    """Render the user's rules as YAML to stdout, or write to --out.

    No --json flag — the OUTPUT is YAML by contract; that's the whole
    point of the command. The wrapped engine call is read-only; failures
    bubble to main() and become exit-1.
    """
    from engine.alert_engine_v2 import load_rules
    from tools.rules_yaml import rules_to_yaml

    rules = load_rules(user_id=args.user_id)
    yaml_text = rules_to_yaml(rules)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(yaml_text)
        # Print the path on stdout so a calling shell script can pipe
        # the result; the YAML itself is on disk and the caller can
        # cat / git-add it.
        print(str(out_path))
    else:
        # Don't add a trailing newline beyond what rules_to_yaml emits
        # — the emitter already terminates with one.
        sys.stdout.write(yaml_text)


def _cmd_rules_import(args: argparse.Namespace) -> None:
    """Read YAML from --in, validate, and save via save_rules
    (overwriting the user's rule set). --dry-run prints what WOULD be
    saved + any warnings without writing.

    Warnings always print to stdout (so a non-interactive caller piping
    output can capture them); errors print to stderr via main()'s
    exit-1 catch.
    """
    from engine.alert_engine_v2 import save_rules
    from tools.rules_yaml import yaml_to_rules

    in_path = Path(args.in_path)
    if not in_path.exists():
        raise RuntimeError(f"input file not found: {in_path}")
    yaml_text = in_path.read_text()
    rules, warnings = yaml_to_rules(yaml_text)

    # If the parser produced no rules AND emitted warnings, treat that
    # as an error — the file is malformed or has no salvageable
    # content. Exit 1 with the first warning on stderr.
    if not rules and warnings:
        raise RuntimeError(f"no importable rules: {warnings[0]}")

    # Surface warnings to stdout in a stable, parseable form. Operators
    # want to see what was defaulted / dropped without scrolling
    # through engine logs.
    for w in warnings:
        print(f"warning: {w}")

    if args.dry_run:
        print(f"would save {len(rules)} rules for user_id={args.user_id!r} (dry-run)")
        for r in rules:
            print(f"  - {r.get('rule_id')}: {r.get('name')}")
        return

    save_rules(rules, user_id=args.user_id)
    print(f"saved {len(rules)} rules for user_id={args.user_id!r}")


def _cmd_rules_diff(args: argparse.Namespace) -> None:
    """Compare the YAML in --in against the user's currently-persisted
    rule set. Surface added / removed / changed in a unified-diff style
    block so the operator can see what an import would do BEFORE
    pulling the trigger.

    Comparison is by rule_id — a rule with the same id in both files is
    "changed" if any field differs; ids present only on one side are
    "added" (in YAML, not in DB) or "removed" (in DB, not in YAML).
    """
    import difflib

    from engine.alert_engine_v2 import load_rules
    from tools.rules_yaml import rules_to_yaml, yaml_to_rules

    in_path = Path(args.in_path)
    if not in_path.exists():
        raise RuntimeError(f"input file not found: {in_path}")
    new_yaml = in_path.read_text()
    new_rules, warnings = yaml_to_rules(new_yaml)
    for w in warnings:
        print(f"warning: {w}")

    current_rules = load_rules(user_id=args.user_id)
    # Render both sides to the canonical YAML so the diff lines up.
    current_yaml = rules_to_yaml(current_rules)
    # rules_to_yaml emits its own normalization (sorted by rule_id,
    # fixed field order), so the diff is deterministic and focuses on
    # actual content changes — not formatting drift.
    rendered_new_yaml = rules_to_yaml(new_rules)

    current_lines = current_yaml.splitlines(keepends=True)
    new_lines = rendered_new_yaml.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        current_lines,
        new_lines,
        fromfile="current",
        tofile=str(in_path),
        n=3,
    ))
    if not diff_lines:
        print("(no changes)")
        return
    sys.stdout.write("".join(diff_lines))


# ─────────────────────────────────────────────────────────────────────────────
#  rules — CSV variant of the config-as-code subcommands.
#
#  Same shape as the YAML triplet above (export → stdout/file, import →
#  parse + save, diff → unified diff vs current). CSV is what most
#  operators reach for first because it opens in Excel; the YAML
#  variant stays for the engineer-friendly round-trip.
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_rules_export_csv(args: argparse.Namespace) -> None:
    """Render the user's rules as CSV to stdout, or write to --out.

    Output carries a UTF-8 BOM so Excel opens it without mojibake on
    macOS / Windows. The wrapped engine call is read-only; failures
    bubble to main() and become exit-1.
    """
    from engine.alert_engine_v2 import load_rules
    from tools.rules_csv import rules_to_csv

    rules = load_rules(user_id=args.user_id)
    csv_text = rules_to_csv(rules)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Write text — Python writes the file as UTF-8 by default on
        # Python 3.9+; the BOM is already part of csv_text so we don't
        # need to encode separately.
        out_path.write_text(csv_text, encoding="utf-8")
        # Path on stdout — matches the pattern used by ``rules export``.
        print(str(out_path))
    else:
        # The CSV already terminates with newlines; no extra trailing
        # newline beyond what rules_to_csv emits.
        sys.stdout.write(csv_text)


def _cmd_rules_import_csv(args: argparse.Namespace) -> None:
    """Read CSV from --in, validate, and save via save_rules
    (overwriting the user's rule set). --dry-run prints what WOULD be
    saved + any warnings without writing.

    Warnings print to stdout (so a non-interactive caller piping the
    output can capture them); errors print to stderr via main()'s
    exit-1 catch.
    """
    from engine.alert_engine_v2 import save_rules
    from tools.rules_csv import csv_to_rules

    in_path = Path(args.in_path)
    if not in_path.exists():
        raise RuntimeError(f"input file not found: {in_path}")
    # Read as text — Python's open() on Python 3.9 defaults to the
    # platform encoding, which can mangle the BOM. Force utf-8 (the
    # parser strips the BOM itself so this is belt-and-braces).
    csv_text = in_path.read_text(encoding="utf-8")
    rules, warnings = csv_to_rules(csv_text)

    # No salvageable content + warnings present → treat as error so the
    # CLI exits 1 with the first warning on stderr. Same posture as
    # the YAML import.
    if not rules and warnings:
        raise RuntimeError(f"no importable rules: {warnings[0]}")

    for w in warnings:
        print(f"warning: {w}")

    if args.dry_run:
        print(
            f"would save {len(rules)} rules for "
            f"user_id={args.user_id!r} (dry-run)"
        )
        for r in rules:
            print(f"  - {r.get('rule_id')}: {r.get('name')}")
        return

    save_rules(rules, user_id=args.user_id)
    print(f"saved {len(rules)} rules for user_id={args.user_id!r}")


def _cmd_rules_diff_csv(args: argparse.Namespace) -> None:
    """Compare the CSV in --in against the user's currently-persisted
    rule set. Surface added / removed / changed in a unified-diff style
    block so the operator can see what an import would do BEFORE
    pulling the trigger.

    The diff renders both sides via ``rules_to_csv`` so the
    comparison is line-aligned on the canonical column order — a
    formatting drift between two CSV files can never appear as a
    spurious diff.
    """
    import difflib

    from engine.alert_engine_v2 import load_rules
    from tools.rules_csv import csv_to_rules, rules_to_csv

    in_path = Path(args.in_path)
    if not in_path.exists():
        raise RuntimeError(f"input file not found: {in_path}")
    new_csv = in_path.read_text(encoding="utf-8")
    new_rules, warnings = csv_to_rules(new_csv)
    for w in warnings:
        print(f"warning: {w}")

    current_rules = load_rules(user_id=args.user_id)
    # Render both sides via rules_to_csv so the diff aligns on the
    # canonical column order — formatting drift between two CSVs
    # cannot surface as a spurious diff.
    current_csv = rules_to_csv(current_rules)
    rendered_new_csv = rules_to_csv(new_rules)

    current_lines = current_csv.splitlines(keepends=True)
    new_lines = rendered_new_csv.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        current_lines,
        new_lines,
        fromfile="current",
        tofile=str(in_path),
        n=3,
    ))
    if not diff_lines:
        print("(no changes)")
        return
    sys.stdout.write("".join(diff_lines))


def _cmd_settings_set(args: argparse.Namespace) -> None:
    from auth.settings import update_setting

    # Build a mapping of (flag → settings key) so the loop below applies
    # only the flags the operator actually passed. ``argparse`` leaves
    # un-provided string args as ``None``; report-window comes through
    # as ``None`` when not provided.
    updates: dict[str, Any] = {}
    if args.timezone is not None:
        updates["timezone"] = args.timezone
    if args.theme is not None:
        updates["theme"] = args.theme
    if args.report_window is not None:
        updates["default_report_window_days"] = int(args.report_window)
    if args.alert_severity is not None:
        updates["default_alert_severity"] = args.alert_severity

    if not updates:
        raise RuntimeError(
            "settings set requires at least one of --timezone / --theme "
            "/ --report-window / --alert-severity"
        )

    applied: dict[str, Any] = {}
    for key, value in updates.items():
        ok = update_setting(args.user_id, key, value)
        applied[key] = bool(ok)

    payload = {"user_id": args.user_id, "applied": applied}
    if args.json:
        _print_json(payload)
    else:
        _print_kv({"user_id": args.user_id})
        _print_kv(applied)


# ─────────────────────────────────────────────────────────────────────────────
#  escalations — per-rule alert-escalation chains (schema v24)
#
#  Mirrors the API + UI surface: list / add / delete (one step) / clear
#  (whole chain). Every subcommand is per-user scoped via the
#  ``--user-id`` flag — the chain owner. ``add`` validates that the
#  supplied ``--channel-id`` exists in the user's delivery-channel set
#  before persisting; targeting another user's channel is rejected at
#  CLI time with a clear error (the engine would silently fail at
#  dispatch later, which is worse UX).
# ─────────────────────────────────────────────────────────────────────────────

def _format_chain_for_table(
    chain: list, channels_by_id: dict[str, Any],
) -> list[dict]:
    """Project a list of EscalationStep onto the row dicts the CLI
    table renderer consumes. ``channels_by_id`` lets the helper turn
    each step's opaque channel_id into a human-readable name when
    available, falling back to "(missing)" when the channel has been
    deleted out from under the chain.

    Returned dicts carry the columns ``chain_id`` / ``step`` /
    ``after_minutes`` / ``channel`` / ``channel_id`` — the same shape
    used by every other ``ops_cli list`` handler so the operator
    output stays consistent.
    """
    rows: list[dict] = []
    for step in chain:
        ch = channels_by_id.get(step.channel_id)
        ch_name = ch.name if ch is not None else "(missing)"
        rows.append({
            "chain_id":       step.chain_id[:10],
            "step":           step.step_number,
            "after_minutes":  step.after_minutes,
            "channel":        ch_name,
            "channel_id":     (step.channel_id or "")[:10],
        })
    return rows


def _cmd_escalations_list(args: argparse.Namespace) -> None:
    """List a rule's escalation chain ordered by step_number ASC."""
    from engine.alert_delivery import load_channels
    from engine.alert_escalation import get_escalation_chain

    chain = get_escalation_chain(args.rule_id, user_id=args.user_id)
    if args.json:
        _print_json(chain)
        return

    channels = load_channels(user_id=args.user_id)
    by_id = {c.channel_id: c for c in channels}
    rows = _format_chain_for_table(chain, by_id)
    _print_table(
        rows,
        columns=["chain_id", "step", "after_minutes", "channel", "channel_id"],
    )


def _cmd_escalations_add(args: argparse.Namespace) -> None:
    """Persist (or replace) one step in a rule's escalation chain.

    Validates ``--channel-id`` against the user's own delivery-channel
    set BEFORE the write so an operator that fat-fingered the id (or
    pointed it at another user's channel) gets a clean error instead
    of a chain step that fails silently at dispatch time.
    """
    from engine.alert_delivery import load_channels
    from engine.alert_escalation import add_escalation_step

    channels = load_channels(user_id=args.user_id)
    by_id = {c.channel_id: c for c in channels}
    if args.channel_id not in by_id:
        raise RuntimeError(
            f"channel_id={args.channel_id!r} not found in user_id="
            f"{args.user_id!r}'s channel set "
            f"(known: {sorted(by_id.keys())[:5]})"
        )

    step = add_escalation_step(
        rule_id=args.rule_id,
        user_id=args.user_id,
        step_number=int(args.step),
        after_minutes=int(args.after_minutes),
        channel_id=args.channel_id,
    )
    if step is None:
        raise RuntimeError(
            f"add_escalation_step failed for rule_id={args.rule_id!r} "
            f"step={args.step} — see logs"
        )

    if args.json:
        _print_json(step)
        return
    _print_kv({
        "chain_id":      step.chain_id,
        "rule_id":       step.rule_id,
        "user_id":       step.user_id,
        "step_number":   step.step_number,
        "after_minutes": step.after_minutes,
        "channel_id":    step.channel_id,
        "created_at":    step.created_at,
    })


def _cmd_escalations_delete(args: argparse.Namespace) -> None:
    """Delete one step by chain_id. Per-user scoped — cross-user
    attempts surface as ``deleted: False`` (exit 0), not a crash."""
    from engine.alert_escalation import delete_escalation_step

    ok = delete_escalation_step(args.chain_id, user_id=args.user_id)
    payload = {"chain_id": args.chain_id, "deleted": bool(ok)}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_escalations_clear(args: argparse.Namespace) -> None:
    """Bulk-delete every step in a rule's chain. Per-user scoped."""
    from engine.alert_escalation import delete_chain

    removed = delete_chain(args.rule_id, user_id=args.user_id)
    payload = {
        "rule_id":       args.rule_id,
        "deleted_steps": int(removed),
    }
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


# ─────────────────────────────────────────────────────────────────────────────
#  Weekly digest — preview / send-now / enable / disable / config
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_digest_preview(args: argparse.Namespace) -> None:
    """Render the digest as markdown to stdout WITHOUT dispatching.

    Operators use this to sanity-check the layout / content before
    enabling the schedule. --json returns the full dataclass + the
    rendered markdown so an automation can pipe the output.
    """
    from engine.weekly_digest import compute_digest, render_digest_markdown

    digest = compute_digest(user_id=args.user_id, week_start=args.week_start)
    if args.json:
        from dataclasses import asdict as _asdict

        _print_json({
            "digest":   _asdict(digest),
            "markdown": render_digest_markdown(digest),
        })
        return
    print(render_digest_markdown(digest))


def _cmd_digest_send_now(args: argparse.Namespace) -> None:
    """Compute + dispatch the digest immediately (bypasses the
    schedule). Useful for "preview by email" runs and post-incident
    summaries that the operator wants out the door before Monday.

    No rate limit, no per-user lock — explicit operator intent always
    wins over the idempotency guard the worker uses.
    """
    from engine.weekly_digest import compute_digest, dispatch_digest

    digest = compute_digest(user_id=args.user_id)
    results = dispatch_digest(digest)
    payload = {
        "user_id":  args.user_id,
        "dispatched": len(results),
        "successes":  sum(1 for r in results if r.get("success")),
        "results":    results,
    }
    if args.json:
        _print_json(payload)
    else:
        _print_kv({k: v for k, v in payload.items() if k != "results"})


def _cmd_digest_enable(args: argparse.Namespace) -> None:
    """Opt the user into the weekly digest.

    --channels is a comma-separated channel_id list. Validation lives
    on the engine side (``enable_digest`` calls the normalizer); a bad
    channel_id quietly drops out at dispatch time.
    """
    from engine.weekly_digest import enable_digest

    ids = [c.strip() for c in (args.channels or "").split(",") if c.strip()]
    ok = enable_digest(
        user_id=args.user_id,
        channel_ids=ids,
        day_of_week=args.day_of_week,
        hour_utc=args.hour,
    )
    if not ok:
        raise RuntimeError(
            f"enable_digest persistence failed for user_id={args.user_id!r}"
        )
    payload = {
        "user_id":     args.user_id,
        "enabled":     True,
        "day_of_week": args.day_of_week,
        "hour_utc":    args.hour,
        "channel_ids": ids,
    }
    if args.json:
        _print_json(payload)
    else:
        _print_kv({
            "user_id":     args.user_id,
            "enabled":     True,
            "day_of_week": args.day_of_week,
            "hour_utc":    args.hour,
            "channels":    ",".join(ids),
        })


def _cmd_digest_disable(args: argparse.Namespace) -> None:
    """Opt the user out. Wipes both the config row AND the per-user
    dispatch lock so a re-enable later does not inherit a stale lock.
    """
    from engine.weekly_digest import disable_digest

    ok = disable_digest(user_id=args.user_id)
    payload = {"user_id": args.user_id, "disabled": bool(ok)}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_digest_config(args: argparse.Namespace) -> None:
    """Print the persisted (or default) digest config for one user."""
    from engine.weekly_digest import get_digest_config

    cfg = get_digest_config(user_id=args.user_id)
    payload = {"user_id": args.user_id, **cfg}
    if args.json:
        _print_json(payload)
    else:
        _print_kv({
            "user_id":     args.user_id,
            "enabled":     cfg.get("enabled", False),
            "day_of_week": cfg.get("day_of_week", "monday"),
            "hour_utc":    cfg.get("hour_utc", 14),
            "channels":    ",".join(cfg.get("channel_ids", []) or []),
        })


# ─────────────────────────────────────────────────────────────────────────────
#  Retries (delivery_retry_queue, schema v26)
# ─────────────────────────────────────────────────────────────────────────────


def _retry_to_dict(entry) -> dict:
    """Project a RetryEntry onto the wire shape — shared by JSON +
    table rendering. Keeps the channel target OFF the wire (only the
    channel_id reference is emitted, never the webhook URL)."""
    return {
        "queue_id":         entry.queue_id,
        "alert_id":         entry.alert_id,
        "channel_id":       entry.channel_id,
        "user_id":          entry.user_id,
        "attempt_count":    entry.attempt_count,
        "last_attempt_at":  entry.last_attempt_at or "",
        "last_error":       (entry.last_error or "")[:100],
        "next_attempt_at":  entry.next_attempt_at,
        "enqueued_at":      entry.enqueued_at,
        "final_status":     entry.final_status,
        "final_at":         entry.final_at or "",
    }


def _cmd_retries_list(args: argparse.Namespace) -> None:
    """List retry-queue rows filtered by --status (default: pending).

    Per-user scoped when --user-id is provided; defaults to "every
    user" so a global operator can audit the entire queue.
    """
    from engine.delivery_retry import (
        list_failed,
        list_pending,
        list_succeeded_recent,
    )

    status = (getattr(args, "status", None) or "pending").lower()
    if status not in ("pending", "failed", "succeeded"):
        print(f"error: --status must be one of pending|failed|succeeded "
              f"(got {status!r})", file=sys.stderr)
        raise SystemExit(2)
    user_id = args.user_id if args.user_id else None
    limit = max(1, int(getattr(args, "limit", 100) or 100))
    if status == "pending":
        entries = list_pending(user_id=user_id, limit=limit)
    elif status == "failed":
        entries = list_failed(user_id=user_id, limit=limit)
    else:
        entries = list_succeeded_recent(user_id=user_id, limit=limit)

    if args.json:
        _print_json([_retry_to_dict(e) for e in entries])
        return
    rows = []
    for e in entries:
        rows.append({
            "queue_id":      e.queue_id[:8],
            "alert_id":      (e.alert_id or "")[:8],
            "channel_id":    (e.channel_id or "")[:8],
            "user_id":       e.user_id,
            "attempt_count": str(e.attempt_count),
            "status":        e.final_status,
            "next_attempt":  e.next_attempt_at,
            "last_error":    (e.last_error or "")[:50],
        })
    _print_table(
        rows,
        columns=[
            "queue_id", "alert_id", "channel_id", "user_id",
            "attempt_count", "status", "next_attempt", "last_error",
        ],
    )


def _cmd_retries_cancel(args: argparse.Namespace) -> None:
    """Cancel one pending retry — marks it failed with the reason
    ``"cancelled by operator"``. Per-user scoped — alice cannot cancel
    bob's retries.
    """
    from engine.delivery_retry import cancel_retry

    ok = cancel_retry(args.queue_id, user_id=args.user_id or "")
    payload = {"queue_id": args.queue_id, "cancelled": bool(ok)}
    if args.json:
        _print_json(payload)
    else:
        if ok:
            print(f"cancelled: {args.queue_id}")
        else:
            print(f"cancel failed (not pending or wrong scope): {args.queue_id}")
    if not ok:
        raise SystemExit(1)


def _cmd_retries_manual(args: argparse.Namespace) -> None:
    """Force the next worker pass to pick a pending retry up
    immediately. Per-user scoped — alice cannot retrigger bob's
    retries.
    """
    from engine.delivery_retry import manual_retry

    ok = manual_retry(args.queue_id, user_id=args.user_id or "")
    payload = {"queue_id": args.queue_id, "manual_retry": bool(ok)}
    if args.json:
        _print_json(payload)
    else:
        if ok:
            print(f"manual retry scheduled: {args.queue_id}")
        else:
            print(f"manual retry failed (not pending or wrong scope): "
                  f"{args.queue_id}")
    if not ok:
        raise SystemExit(1)


def _cmd_retries_cleanup(args: argparse.Namespace) -> None:
    """Operator-triggered cleanup of completed retries past the
    retention window. Pending rows are NEVER swept regardless of age.
    """
    from engine.delivery_retry import cleanup_completed

    retention = int(getattr(args, "retention_days", 14) or 14)
    deleted = cleanup_completed(retention_days=retention)
    payload = {"deleted": deleted, "retention_days": retention}
    if args.json:
        _print_json(payload)
    else:
        print(f"deleted {deleted} completed rows (retention_days={retention})")


def _cmd_retries_process(args: argparse.Namespace) -> None:
    """Run one retry pass NOW (synchronous). Useful for an operator
    who fixed a downstream issue and wants to drain the queue without
    waiting for the next scheduled tick.
    """
    from engine.delivery_retry import run_retry_pass

    counts = run_retry_pass()
    if args.json:
        _print_json(counts)
    else:
        _print_kv(counts)


# ─────────────────────────────────────────────────────────────────────────────
#  Calendar (ICS feed subscription tokens, schema-bumpless)
#
#  Four subcommands cover the operator surface for the per-user
#  ``/api/v1/incidents.ics`` subscription URL: show / generate / revoke
#  the token, and export the ICS body to a file for an air-gapped
#  one-shot import. The token IS the secret — there is no bearer
#  header; the URL itself authenticates the calendar app.
# ─────────────────────────────────────────────────────────────────────────────


def _cmd_calendar_token_show(args: argparse.Namespace) -> None:
    """Print the current calendar token for a user (no DB mutation).

    Prints ``(none)`` when no token has been generated yet — the
    operator can follow up with ``calendar token-generate``.
    """
    from auth.calendar_tokens import get_calendar_token

    tok = get_calendar_token(user_id=args.user_id)
    payload = {"user_id": args.user_id, "calendar_token": tok or ""}
    if args.json:
        _print_json(payload)
    else:
        if tok:
            _print_kv(payload)
        else:
            print(f"user_id   : {args.user_id}")
            print("calendar_token : (none — run `calendar token-generate`)")


def _cmd_calendar_token_generate(args: argparse.Namespace) -> None:
    """Generate (or rotate) the calendar subscription token for a user.

    REPLACES any existing token — the old subscription URL stops
    working immediately. Returns the raw token so the operator can
    paste it into the user's calendar app or hand it over via a
    secure channel.
    """
    from auth.calendar_tokens import generate_calendar_token

    tok = generate_calendar_token(user_id=args.user_id)
    if tok is None:
        raise RuntimeError(
            f"generate_calendar_token failed for user_id={args.user_id!r} "
            "— see logs"
        )
    payload = {"user_id": args.user_id, "calendar_token": tok}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_calendar_token_revoke(args: argparse.Namespace) -> None:
    """Clear the saved calendar token for a user.

    Calendar apps using the old URL will start failing on their
    next refresh tick. Returns ``revoked: False`` (exit 0) when
    no token was set for the user — not an error, just a no-op.
    """
    from auth.calendar_tokens import revoke_calendar_token

    ok = revoke_calendar_token(user_id=args.user_id)
    payload = {"user_id": args.user_id, "revoked": bool(ok)}
    if args.json:
        _print_json(payload)
    else:
        _print_kv(payload)


def _cmd_calendar_export(args: argparse.Namespace) -> None:
    """Render the user's incident feed to an .ics string (stdout or --out FILE).

    Useful for an offline / air-gapped one-shot import — the
    operator emails the .ics to a user whose calendar app cannot
    reach the live subscription URL.

    Does NOT require a calendar token (we already have the
    --user-id from the CLI, which is authority enough — the
    operator running ``ops`` has DB access).
    """
    from engine.alert_correlator import get_recent_incidents
    from utils.ics_export import incidents_to_ics

    window = int(getattr(args, "window", 30) or 30)
    if window < 1 or window > 365:
        window = 30
    incidents = get_recent_incidents(window_days=window, user_id=args.user_id)
    ics_text = incidents_to_ics(incidents)
    out_path = getattr(args, "out", None)
    if out_path:
        # Binary mode — the renderer already terminates lines with
        # CRLF per RFC 5545, and text-mode writes would re-encode
        # to platform line endings on Windows.
        from pathlib import Path
        Path(out_path).write_bytes(ics_text.encode("utf-8"))
        payload = {
            "user_id":   args.user_id,
            "out":       out_path,
            "bytes":     len(ics_text.encode("utf-8")),
            "incidents": len(incidents),
        }
        if args.json:
            _print_json(payload)
        else:
            _print_kv(payload)
    else:
        # Stdout — let the operator pipe into a file or further
        # tooling. Use sys.stdout.buffer to preserve the CRLF
        # line endings (text-mode would normalize on some shells).
        sys.stdout.write(ics_text)


# ─────────────────────────────────────────────────────────────────────────────
#  Disruption subcommands — backtest the SSI against historical events;
#  print template-based "why this route is stressed" explanations.
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_disruption_backtest(args: argparse.Namespace) -> None:
    """Run the SSI backtester. With no event_id -> summary across all events.

    The backtest is *event-aware-synthetic*: SSI inputs are built to REFLECT
    the named historical event (chokepoint disruption, port congestion, rate
    spike) and the SSI's mathematical response is scored. It is NOT a
    time-machine replay of historical state — see docs/DISRUPTION_ALPHA.md.
    """
    from data.historical_events import EVENTS_BY_ID, get_event
    from processing.disruption_backtest import (
        backtest_all_events,
        backtest_event,
    )

    event_id = getattr(args, "event_id", None)
    threshold = getattr(args, "threshold", "Stressed") or "Stressed"
    window = int(getattr(args, "window", 14) or 14)

    if event_id:
        event = get_event(event_id)
        if event is None:
            available = ", ".join(sorted(EVENTS_BY_ID.keys()))
            print(
                f"error: unknown event_id '{event_id}'. Known: {available}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        result = backtest_event(
            event,
            evaluation_window_days=window,
            threshold_band=threshold,
        )
        if args.json:
            _print_json(result)
        else:
            _print_kv(
                {
                    "event_id":            result.event_id,
                    "event_name":          result.event_name,
                    "event_start":         result.event_start,
                    "detected":            result.detected,
                    "detection_band":      result.detection_band,
                    "expected_band":       result.expected_band,
                    "lead_time_days":      result.lead_time_days,
                    "max_score_in_window": result.max_score_in_window,
                    "dominant_component":  result.dominant_component,
                    "per_route_scores":    result.per_route_scores,
                }
            )
        return

    summary = backtest_all_events(
        evaluation_window_days=window,
        threshold_band=threshold,
    )
    if args.json:
        _print_json(summary)
        return

    _print_kv(
        {
            "total_events":          summary.total_events,
            "detected":              summary.detected,
            "early":                 summary.early,
            "hit_rate":              summary.hit_rate,
            "early_rate":            summary.early_rate,
            "mean_lead_time_days":   summary.mean_lead_time_days,
        }
    )
    print("")
    rows = [
        {
            "event_id":           r.event_id,
            "detected":           "Y" if r.detected else "N",
            "band":               r.detection_band,
            "expected":           r.expected_band,
            "max_score":          r.max_score_in_window,
            "dominant":           r.dominant_component,
        }
        for r in summary.results
    ]
    _print_table(
        rows,
        columns=["event_id", "detected", "band", "expected", "max_score", "dominant"],
    )


def _cmd_disruption_explain_routes(args: argparse.Namespace) -> None:
    """Compute the SSI and print top-N route explanations.

    Uses an empty market-input bundle so the SSI's standalone components
    (chokepoint + weather + vulnerability — module-state-driven) speak for
    themselves. For a richer read use the Streamlit Disruption Radar.
    """
    from engine.disruption_explainer import explain_top_disruptions
    from processing.shipping_stress_index import compute_shipping_stress

    top_n = int(getattr(args, "top", 5) or 5)
    report = compute_shipping_stress({}, {}, [], [], voyage_fleet=None)
    explanations = explain_top_disruptions(report.route_stress, top_n=top_n)

    if args.json:
        _print_json(explanations)
        return

    if not explanations:
        print("No stressed routes detected.")
        return

    for ex in explanations:
        print(f"[{ex.severity_band}] {ex.headline}")
        for bullet in ex.why:
            print(f"  - {bullet}")
        if ex.recommended_focus:
            print(f"  focus: {ex.recommended_focus}")
        print("")


# ─────────────────────────────────────────────────────────────────────────────
#  Argparse wiring
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level parser + every subparser.

    Each subcommand carries:
      * its own ``--json`` flag (parser-level so the help text shows it),
      * a ``func=`` default that ``main`` dispatches against,
      * any subcommand-specific positional / option args.

    The parser is built fresh on each call so tests can build a sandbox
    parser without polluting module state."""
    p = argparse.ArgumentParser(
        prog="python -m tools.ops",
        description="Operator CLI for Ship Tracker admin tasks.",
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # ── status ────────────────────────────────────────────────────────────
    s = sub.add_parser("status", help="Show schema version + counts")
    s.add_argument("--json", action="store_true", help="JSON output")
    s.set_defaults(func=_cmd_status)

    # ── alerts ────────────────────────────────────────────────────────────
    p_alerts = sub.add_parser("alerts", help="Alert subcommands")
    sa = p_alerts.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    sl = sa.add_parser("list", help="List recent alerts")
    sl.add_argument("--severity", choices=["CRITICAL", "HIGH", "MEDIUM", "LOW"], default=None)
    sl.add_argument("--limit", type=int, default=20)
    sl.add_argument("--json", action="store_true")
    sl.set_defaults(func=_cmd_alerts_list)

    sa1 = sa.add_parser("ack", help="Acknowledge a single alert")
    sa1.add_argument("alert_id")
    sa1.add_argument("--json", action="store_true")
    sa1.set_defaults(func=_cmd_alerts_ack)

    sa2 = sa.add_parser("ack-all", help="Acknowledge every unread alert")
    sa2.add_argument("--json", action="store_true")
    sa2.set_defaults(func=_cmd_alerts_ack_all)

    sa3 = sa.add_parser("metrics", help="Aggregate acknowledgement metrics")
    sa3.add_argument("--window", type=int, default=30, help="Look-back window in days")
    sa3.add_argument("--json", action="store_true")
    sa3.set_defaults(func=_cmd_alerts_metrics)

    # ── channels ──────────────────────────────────────────────────────────
    p_ch = sub.add_parser("channels", help="Delivery-channel subcommands")
    sc = p_ch.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    sc1 = sc.add_parser("list", help="List delivery channels")
    sc1.add_argument("--json", action="store_true")
    sc1.set_defaults(func=_cmd_channels_list)

    sc2 = sc.add_parser("delete", help="Delete a delivery channel")
    sc2.add_argument("channel_id")
    sc2.add_argument("--json", action="store_true")
    sc2.set_defaults(func=_cmd_channels_delete)

    # ── channels usage / reset-usage / set-budget (schema v25) ──────────────
    # Per-channel monthly delivery budgets. ``usage`` reads the rolling
    # counter, ``reset-usage`` zeros it for a single channel, and
    # ``set-budget`` updates the cap on a channel without going through
    # the UI form. All three accept --user-id so an operator running
    # against another user's scope can manage budgets without logging in.
    sc3 = sc.add_parser(
        "usage",
        help="Show per-channel monthly delivery usage",
    )
    sc3.add_argument(
        "--user-id", default="",
        help="Scope to a user_id ('' = legacy bucket)",
    )
    sc3.add_argument("--json", action="store_true")
    sc3.set_defaults(func=_cmd_channels_usage)

    sc4 = sc.add_parser(
        "reset-usage",
        help="Zero the monthly counter for one channel",
    )
    sc4.add_argument("channel_id")
    sc4.add_argument(
        "--user-id", default="",
        help="Scope to a user_id ('' = legacy bucket)",
    )
    sc4.add_argument("--json", action="store_true")
    sc4.set_defaults(func=_cmd_channels_reset_usage)

    sc5 = sc.add_parser(
        "set-budget",
        help="Set the monthly delivery budget for one channel (0 = unlimited)",
    )
    sc5.add_argument("channel_id")
    sc5.add_argument(
        "--budget", type=int, required=True,
        help="New monthly delivery cap (0 = unlimited)",
    )
    sc5.add_argument(
        "--user-id", default="",
        help="Scope to a user_id ('' = legacy bucket)",
    )
    sc5.add_argument("--json", action="store_true")
    sc5.set_defaults(func=_cmd_channels_set_budget)

    # ── channels failures / reset-failures (auto-disable circuit breaker) ──
    # Per-channel consecutive-failure tracking. ``failures`` lists every
    # channel's current count alongside the auto-disable threshold;
    # ``reset-failures`` zeros one channel's counter (and clears the
    # auto-disabled flag) so the breaker re-arms from scratch. Both
    # accept --user-id for cross-user operator access.
    sc6 = sc.add_parser(
        "failures",
        help="Show per-channel consecutive-failure counters",
    )
    sc6.add_argument(
        "--user-id", default="",
        help="Scope to a user_id ('' = legacy bucket)",
    )
    sc6.add_argument("--json", action="store_true")
    sc6.set_defaults(func=_cmd_channels_failures)

    sc7 = sc.add_parser(
        "reset-failures",
        help="Zero the consecutive-failure counter for one channel",
    )
    sc7.add_argument("channel_id")
    sc7.add_argument(
        "--user-id", default="",
        help="Scope to a user_id ('' = legacy bucket)",
    )
    sc7.add_argument("--json", action="store_true")
    sc7.set_defaults(func=_cmd_channels_reset_failures)

    # ── reports ───────────────────────────────────────────────────────────
    p_rp = sub.add_parser("reports", help="Report-history subcommands")
    sr = p_rp.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    sr1 = sr.add_parser("list", help="List saved reports")
    sr1.add_argument("--limit", type=int, default=10)
    sr1.add_argument("--json", action="store_true")
    sr1.set_defaults(func=_cmd_reports_list)

    sr2 = sr.add_parser("delete", help="Delete a saved report")
    sr2.add_argument("report_id")
    sr2.add_argument("--json", action="store_true")
    sr2.set_defaults(func=_cmd_reports_delete)

    sr3 = sr.add_parser("stats", help="Report-history aggregate stats")
    sr3.add_argument("--json", action="store_true")
    sr3.set_defaults(func=_cmd_reports_stats)

    sr4 = sr.add_parser(
        "diff",
        help="Compare two reports and print the structured diff",
    )
    sr4.add_argument("report_id_a", help="ID of the older / baseline report")
    sr4.add_argument("report_id_b", help="ID of the newer / current report")
    sr4.add_argument(
        "--user-id", dest="user_id", default=None,
        help="Limit lookup to a specific user's scope (default: empty / legacy)",
    )
    sr4.add_argument(
        "--format", choices=["md", "json"], default="md",
        help="Output format (default: md)",
    )
    sr4.set_defaults(func=_cmd_reports_diff)

    # ── telemetry ─────────────────────────────────────────────────────────
    p_tl = sub.add_parser("telemetry", help="LLM-telemetry subcommands")
    st = p_tl.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    st1 = st.add_parser("usage", help="LLM usage summary over a window")
    st1.add_argument("--window", type=int, default=7)
    st1.add_argument("--json", action="store_true")
    st1.set_defaults(func=_cmd_telemetry_usage)

    st2 = st.add_parser("recent", help="Recent LLM calls")
    st2.add_argument("--limit", type=int, default=20)
    st2.add_argument("--json", action="store_true")
    st2.set_defaults(func=_cmd_telemetry_recent)

    st3 = st.add_parser("prune", help="Prune old LLM-call rows")
    st3.add_argument("--retention", type=int, default=90)
    st3.add_argument("--json", action="store_true")
    st3.set_defaults(func=_cmd_telemetry_prune)

    # ── perf ──────────────────────────────────────────────────────────────
    p_pf = sub.add_parser("perf", help="Render-performance subcommands")
    sp = p_pf.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    sp1 = sp.add_parser("summary", help="Render-performance summary")
    sp1.add_argument("--window-hours", type=int, default=24)
    sp1.add_argument("--json", action="store_true")
    sp1.set_defaults(func=_cmd_perf_summary)

    # ── health ────────────────────────────────────────────────────────────
    p_hl = sub.add_parser("health", help="Data-source health subcommands")
    sh = p_hl.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    sh1 = sh.add_parser("summary", help="Data-source health summary")
    sh1.add_argument("--json", action="store_true")
    sh1.set_defaults(func=_cmd_health_summary)

    sh2 = sh.add_parser("ping", help="Ping every data source NOW")
    sh2.add_argument("--json", action="store_true")
    sh2.set_defaults(func=_cmd_health_ping)

    # ── health-alerts ─────────────────────────────────────────────────────
    p_ha = sub.add_parser(
        "health-alerts",
        help="Auto-alerting for degraded data sources",
    )
    sha = p_ha.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    sha1 = sha.add_parser("status", help="Show config + recent fire count")
    sha1.add_argument("--json", action="store_true")
    sha1.set_defaults(func=_cmd_health_alerts_status)

    sha2 = sha.add_parser("enable", help="Turn auto-alerting ON")
    sha2.add_argument("--json", action="store_true")
    sha2.set_defaults(func=_cmd_health_alerts_enable)

    sha3 = sha.add_parser("disable", help="Turn auto-alerting OFF")
    sha3.add_argument("--json", action="store_true")
    sha3.set_defaults(func=_cmd_health_alerts_disable)

    sha4 = sha.add_parser("run-once", help="Run the alerter NOW (for testing)")
    sha4.add_argument("--json", action="store_true")
    sha4.set_defaults(func=_cmd_health_alerts_run_once)

    # ── perf-budgets ──────────────────────────────────────────────────────
    p_pb = sub.add_parser(
        "perf-budgets",
        help="Per-tab render-latency budget admin",
    )
    spb = p_pb.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    spb1 = spb.add_parser("list", help="Show every budget + current p95 + status")
    spb1.add_argument("--json", action="store_true")
    spb1.set_defaults(func=_cmd_perf_budgets_list)

    spb2 = spb.add_parser("set", help="Set / replace the budget for one tab")
    spb2.add_argument("tab_module")
    spb2.add_argument("--max-p95", dest="max_p95", type=float, required=True,
                      help="Max acceptable p95 render time in SECONDS")
    spb2.add_argument("--json", action="store_true")
    spb2.set_defaults(func=_cmd_perf_budgets_set)

    spb3 = spb.add_parser("reset", help="Wipe customisations, revert to defaults")
    spb3.add_argument("--json", action="store_true")
    spb3.set_defaults(func=_cmd_perf_budgets_reset)

    spb4 = spb.add_parser("check", help="Run check_and_alert NOW (prints count dict)")
    spb4.add_argument("--json", action="store_true")
    spb4.set_defaults(func=_cmd_perf_budgets_check)

    # ── anomalies ────────────────────────────────────────────────────────
    p_an = sub.add_parser(
        "anomalies",
        help="Time-series anomaly detection (BDI, FBX, SCFI, WTI, ...)",
    )
    sa = p_an.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    sa1 = sa.add_parser("check", help="Run detection NOW + print results")
    sa1.add_argument("--user-id", dest="user_id", default=None,
                     help="User to scope cooldown to (default: current session)")
    sa1.add_argument("--json", action="store_true")
    sa1.set_defaults(func=_cmd_anomalies_check)

    sa2 = sa.add_parser("configs", help="Show the current per-metric configs")
    sa2.add_argument("--user-id", dest="user_id", default=None)
    sa2.add_argument("--json", action="store_true")
    sa2.set_defaults(func=_cmd_anomalies_configs)

    sa3 = sa.add_parser("enable", help="Enable detection for one metric")
    sa3.add_argument("metric_id")
    sa3.add_argument("--user-id", dest="user_id", default=None)
    sa3.add_argument("--json", action="store_true")
    sa3.set_defaults(func=_cmd_anomalies_enable)

    sa4 = sa.add_parser("disable", help="Disable detection for one metric")
    sa4.add_argument("metric_id")
    sa4.add_argument("--user-id", dest="user_id", default=None)
    sa4.add_argument("--json", action="store_true")
    sa4.set_defaults(func=_cmd_anomalies_disable)

    sa5 = sa.add_parser("set", help="Update z-threshold / lookback / method on a metric")
    sa5.add_argument("metric_id")
    sa5.add_argument("--z-threshold", dest="z_threshold", type=float, default=None,
                     help="Replacement z-threshold (or %% for pct_drift method)")
    sa5.add_argument("--lookback-days", dest="lookback_days", type=int, default=None,
                     help="Replacement lookback window")
    sa5.add_argument("--method", dest="method", default=None,
                     choices=["zscore", "pct_drift", "rolling_mean_deviation"],
                     help="Replacement detection method")
    sa5.add_argument("--user-id", dest="user_id", default=None)
    sa5.add_argument("--json", action="store_true")
    sa5.set_defaults(func=_cmd_anomalies_set)

    # ── users ─────────────────────────────────────────────────────────────
    p_us = sub.add_parser("users", help="User-account subcommands")
    su = p_us.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    su1 = su.add_parser("list", help="List registered users")
    su1.add_argument("--json", action="store_true")
    su1.set_defaults(func=_cmd_users_list)

    su2 = su.add_parser("create", help="Create a new user")
    su2.add_argument("username")
    su2.add_argument("--password", required=True, help="Min 8 chars")
    su2.add_argument("--json", action="store_true")
    su2.set_defaults(func=_cmd_users_create)

    su3 = su.add_parser("disable", help="Disable an account (kill-switch)")
    su3.add_argument("user_id", help="The user_id to disable")
    su3.add_argument("--json", action="store_true")
    su3.set_defaults(func=_cmd_users_disable)

    su4 = su.add_parser("enable", help="Re-enable a disabled account")
    su4.add_argument("user_id", help="The user_id to enable")
    su4.add_argument("--json", action="store_true")
    su4.set_defaults(func=_cmd_users_enable)

    # ── tokens ────────────────────────────────────────────────────────────
    p_tk = sub.add_parser("tokens", help="API-token subcommands")
    sk = p_tk.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    sk1 = sk.add_parser("list", help="List a user's API tokens")
    sk1.add_argument("--user-id", dest="user_id", required=True)
    sk1.add_argument("--json", action="store_true")
    sk1.set_defaults(func=_cmd_tokens_list)

    sk2 = sk.add_parser("create", help="Create an API token")
    sk2.add_argument("user_id")
    sk2.add_argument("--label", required=True)
    sk2.add_argument(
        "--expires-in-days", dest="expires_in_days", type=int, default=None,
        help=(
            "Token lifetime in days. Omit to use API_TOKEN_TTL_DAYS or the "
            "90-day default; pass 0 for a non-expiring token."
        ),
    )
    sk2.add_argument("--json", action="store_true")
    sk2.set_defaults(func=_cmd_tokens_create)

    sk3 = sk.add_parser("revoke", help="Revoke an API token")
    sk3.add_argument("token_id")
    sk3.add_argument("--user-id", dest="user_id", required=True)
    sk3.add_argument("--json", action="store_true")
    sk3.set_defaults(func=_cmd_tokens_revoke)

    # ── export ────────────────────────────────────────────────────────────
    se = sub.add_parser("export", help="Build a bulk-state tar.gz archive")
    se.add_argument("--output", default=None, help="Custom output path")
    se.add_argument("--json", action="store_true")
    se.set_defaults(func=_cmd_export)

    # ── audit ─────────────────────────────────────────────────────────────
    # ``audit export`` writes audit_events rows as JSONL (one JSON
    # object per line) for SIEM ingestion. Default is stdout; --out
    # writes a file via the streaming variant. Filters mirror what
    # query_audit accepts; --until is applied client-side since the
    # underlying engine call only natively supports --since.
    p_au = sub.add_parser("audit", help="Audit-log subcommands")
    au_sub = p_au.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    au1 = au_sub.add_parser(
        "export",
        help="Export audit_events as JSONL (one JSON object per line)",
    )
    au1.add_argument(
        "--user-id", dest="user_id", default=None,
        help="Filter to a single user_id (default: every user)",
    )
    au1.add_argument(
        "--action", default=None,
        help="Filter to a single action verb (e.g. login_success)",
    )
    au1.add_argument(
        "--since", default=None,
        help="ISO-8601 lower bound on created_at (inclusive)",
    )
    au1.add_argument(
        "--until", default=None,
        help="ISO-8601 upper bound on created_at (exclusive)",
    )
    au1.add_argument(
        "--limit", type=int, default=10_000,
        help="Cap rows returned (default: 10000)",
    )
    au1.add_argument(
        "--out", default=None,
        help="Write JSONL to this path (default: stdout)",
    )
    au1.set_defaults(func=_cmd_audit_export)

    # ── mfa ───────────────────────────────────────────────────────────────
    p_mfa = sub.add_parser("mfa", help="TOTP second-factor subcommands")
    sm = p_mfa.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    sm1 = sm.add_parser("enable", help="Generate a secret + enable MFA for a user")
    sm1.add_argument("user_id")
    sm1.add_argument(
        "--secret",
        help=(
            "Use this base32 secret instead of generating one. Pair with "
            "--code to do a real proof-of-possession enable (the user reads "
            "you a code generated from this secret)."
        ),
    )
    sm1.add_argument(
        "--code",
        help=(
            "A current TOTP code proving possession of --secret. When given, "
            "the enable is REFUSED unless the code verifies. Omit for the "
            "generate-and-go provisioning path (enable without proof, logged)."
        ),
    )
    sm1.add_argument("--json", action="store_true")
    sm1.set_defaults(func=_cmd_mfa_enable)

    sm2 = sm.add_parser("disable", help="Clear the MFA secret + flag for a user")
    sm2.add_argument("user_id")
    sm2.add_argument("--json", action="store_true")
    sm2.set_defaults(func=_cmd_mfa_disable)

    sm3 = sm.add_parser("status", help="Show whether MFA is enabled for a user")
    sm3.add_argument("user_id")
    sm3.add_argument("--json", action="store_true")
    sm3.set_defaults(func=_cmd_mfa_status)

    sm4 = sm.add_parser(
        "recovery-codes",
        help="Show the count of UNUSED recovery codes (does NOT print the codes)",
    )
    sm4.add_argument("user_id")
    sm4.add_argument("--json", action="store_true")
    sm4.set_defaults(func=_cmd_mfa_recovery_codes)

    sm5 = sm.add_parser(
        "regenerate-codes",
        help="Wipe existing recovery codes and mint a fresh batch (prints codes ONCE)",
    )
    sm5.add_argument("user_id")
    sm5.add_argument("--json", action="store_true")
    sm5.set_defaults(func=_cmd_mfa_regenerate_codes)

    # ── invite ────────────────────────────────────────────────────────────
    p_inv = sub.add_parser("invite", help="User-invitation subcommands")
    siv = p_inv.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    siv1 = siv.add_parser(
        "create",
        help="Mint a new invitation (prints the token ONCE)",
    )
    siv1.add_argument(
        "--invited-by",
        dest="invited_by",
        required=True,
        help="user_id of the admin issuing the invite (stamped on the row)",
    )
    siv1.add_argument(
        "--email",
        default=None,
        help="Optional — bind the invite to this email (signup username must match)",
    )
    siv1.add_argument(
        "--role",
        default="user",
        choices=["user", "admin"],
        help="Role the invite grants on consumption (default: user)",
    )
    siv1.add_argument(
        "--expires-days",
        dest="expires_days",
        default=7,
        type=int,
        help="Invite lifetime in days (default: 7)",
    )
    siv1.add_argument("--json", action="store_true")
    siv1.set_defaults(func=_cmd_invite_create)

    siv2 = siv.add_parser("list", help="List invitations (unconsumed by default)")
    siv2.add_argument(
        "--invited-by",
        dest="invited_by",
        default=None,
        help="Optional — filter to invitations issued by this user_id",
    )
    siv2.add_argument(
        "--include-consumed",
        dest="include_consumed",
        action="store_true",
        help="Include already-consumed invitations in the listing",
    )
    siv2.add_argument("--json", action="store_true")
    siv2.set_defaults(func=_cmd_invite_list)

    siv3 = siv.add_parser(
        "revoke",
        help="Delete an UNCONSUMED invitation (consumed rows are immutable)",
    )
    siv3.add_argument("invite_id")
    siv3.add_argument("--json", action="store_true")
    siv3.set_defaults(func=_cmd_invite_revoke)

    # ── filters ───────────────────────────────────────────────────────────
    p_fl = sub.add_parser("filters", help="Saved filter-preset subcommands")
    sf = p_fl.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    sf1 = sf.add_parser("list", help="List a user's saved filter presets")
    sf1.add_argument("--user-id", dest="user_id", required=True)
    sf1.add_argument("--scope", default=None, help="Filter by preset scope (e.g. alerts)")
    sf1.add_argument("--json", action="store_true")
    sf1.set_defaults(func=_cmd_filters_list)

    sf2 = sf.add_parser("delete", help="Delete one preset by name+scope")
    sf2.add_argument("name")
    sf2.add_argument("--scope", required=True)
    sf2.add_argument("--user-id", dest="user_id", required=True)
    sf2.add_argument("--json", action="store_true")
    sf2.set_defaults(func=_cmd_filters_delete)

    # ── incidents ─────────────────────────────────────────────────────────
    p_in = sub.add_parser("incidents", help="Alert-correlation subcommands")
    si = p_in.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    si1 = si.add_parser("list", help="List recent correlated incidents")
    si1.add_argument("--window", type=int, default=7)
    si1.add_argument("--json", action="store_true")
    si1.set_defaults(func=_cmd_incidents_list)

    si2 = si.add_parser("stats", help="Aggregate incident summary stats")
    si2.add_argument("--window", type=int, default=7)
    si2.add_argument("--json", action="store_true")
    si2.set_defaults(func=_cmd_incidents_stats)

    # ── settings ──────────────────────────────────────────────────────────
    p_st = sub.add_parser("settings", help="Per-user preferences subcommands")
    ss = p_st.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    ss1 = ss.add_parser("show", help="Show a user's saved preferences (or defaults)")
    ss1.add_argument("--user-id", dest="user_id", required=True)
    ss1.add_argument("--json", action="store_true")
    ss1.set_defaults(func=_cmd_settings_show)

    ss2 = ss.add_parser("set", help="Update one or more preference keys")
    ss2.add_argument("--user-id", dest="user_id", required=True)
    ss2.add_argument("--timezone", default=None, help="IANA timezone (e.g. America/New_York)")
    ss2.add_argument("--theme", default=None, choices=["auto", "light", "dark"])
    ss2.add_argument("--report-window", dest="report_window", default=None, type=int,
                     help="Default report window in days")
    ss2.add_argument("--alert-severity", dest="alert_severity", default=None,
                     choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"])
    ss2.add_argument("--json", action="store_true")
    ss2.set_defaults(func=_cmd_settings_set)

    # ── prefs ─────────────────────────────────────────────────────────────
    # Per-user notification preferences (auth.notification_prefs).
    # Mirrors the API + UI surface — show / set / reset. Every
    # subcommand is per-user scoped via the required ``--user-id`` flag;
    # alice can't show, set, or reset bob's prefs from the CLI.
    p_pr = sub.add_parser("prefs", help="Per-user notification-prefs subcommands")
    sp = p_pr.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    sp1 = sp.add_parser("show", help="Pretty-print one user's prefs (or defaults)")
    sp1.add_argument("--user-id", dest="user_id", required=True)
    sp1.add_argument("--json", action="store_true")
    sp1.set_defaults(func=_cmd_prefs_show)

    sp2 = sp.add_parser("set", help="Update one or more pref fields")
    sp2.add_argument("--user-id", dest="user_id", required=True)
    sp2.add_argument(
        "--enabled", default=None,
        help="Master switch ('true' or 'false')",
    )
    sp2.add_argument(
        "--min-severity", dest="min_severity", default=None,
        choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        help="Floor — alerts below this severity are suppressed",
    )
    sp2.add_argument(
        "--alert-types", dest="alert_types", default=None,
        help=('Comma-separated allow-list (e.g. "BDI_MOVE,MACRO"). '
              'Empty string clears the filter.'),
    )
    sp2.add_argument(
        "--quiet-start", dest="quiet_start", default=None, type=int,
        help="Quiet window start hour (UTC, 0–23). Pair with --quiet-end.",
    )
    sp2.add_argument(
        "--quiet-end", dest="quiet_end", default=None, type=int,
        help="Quiet window end hour (UTC, 0–23). Pair with --quiet-start.",
    )
    sp2.add_argument(
        "--clear-quiet-hours", dest="clear_quiet_hours", action="store_true",
        help="Disable quiet hours entirely (overrides --quiet-* on same call).",
    )
    sp2.add_argument("--json", action="store_true")
    sp2.set_defaults(func=_cmd_prefs_set)

    sp3 = sp.add_parser("reset", help="Wipe one user's prefs back to defaults")
    sp3.add_argument("--user-id", dest="user_id", required=True)
    sp3.add_argument("--json", action="store_true")
    sp3.set_defaults(func=_cmd_prefs_reset)

    # ── silences ──────────────────────────────────────────────────────────
    # Bounded alert silencing for planned downtime. Three subcommands —
    # list / create / delete. The create command's match keys
    # (--rule-id / --ticker / --severity) are all optional and NULL on
    # the row means "matches any value for this column"; the broadest
    # silence (all three omitted) shuts up every alert for the user.
    p_sil = sub.add_parser("silences", help="Alert-silence subcommands")
    ssil = p_sil.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    ssil1 = ssil.add_parser("list", help="List a user's silences")
    ssil1.add_argument(
        "--user-id", dest="user_id", required=True,
        help="User scope — alice cannot list bob's silences",
    )
    ssil1.add_argument(
        "--include-expired", dest="include_expired", action="store_true",
        help="Include expired silences (kept around for audit retention)",
    )
    ssil1.add_argument("--json", action="store_true")
    ssil1.set_defaults(func=_cmd_silences_list)

    ssil2 = ssil.add_parser(
        "create", help="Create a new silence (operator + duration + filters)",
    )
    ssil2.add_argument(
        "--user-id", dest="user_id", required=True,
        help="Owner of the silence — alerts under this user_id are muted",
    )
    ssil2.add_argument(
        "--duration-minutes", dest="duration_minutes", required=True, type=int,
        help="Silence lifetime in minutes (clamped to >= 1)",
    )
    ssil2.add_argument(
        "--rule-id", dest="rule_id", default=None,
        help='Restrict to one rule_id (omit = "all rules")',
    )
    ssil2.add_argument(
        "--ticker", default=None,
        help='Restrict to one ticker (omit = "all tickers")',
    )
    ssil2.add_argument(
        "--severity", default=None,
        choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        help='Restrict to one severity (omit = "all severities")',
    )
    ssil2.add_argument(
        "--reason", default=None,
        help='Free-form operator note ("FRED maintenance")',
    )
    ssil2.add_argument("--json", action="store_true")
    ssil2.set_defaults(func=_cmd_silences_create)

    ssil3 = ssil.add_parser("delete", help="Cancel a silence early")
    ssil3.add_argument("silence_id")
    ssil3.add_argument(
        "--user-id", dest="user_id", required=True,
        help="User scope — alice cannot delete bob's silences",
    )
    ssil3.add_argument("--json", action="store_true")
    ssil3.set_defaults(func=_cmd_silences_delete)

    # ── annotations ───────────────────────────────────────────────────────
    # Per-alert operator commentary threads (schema v23). Three
    # subcommands — list / add / delete. Per-user scoped via
    # ``--user-id`` (the alert owner); the CLI stamps
    # author_user_id == --user-id so the running operator is the
    # author of every note they create.
    p_ann = sub.add_parser("annotations", help="Alert-annotation subcommands")
    sann = p_ann.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    sann1 = sann.add_parser(
        "list", help="List the annotation thread for one alert",
    )
    sann1.add_argument(
        "alert_id",
        help="Alert id whose annotation thread to list",
    )
    sann1.add_argument(
        "--user-id", dest="user_id", required=True,
        help="User scope — alice cannot see bob's alert annotations",
    )
    sann1.add_argument("--json", action="store_true")
    sann1.set_defaults(func=_cmd_annotations_list)

    sann2 = sann.add_parser(
        "add", help="Add one annotation to an alert",
    )
    sann2.add_argument(
        "alert_id",
        help="Alert id the annotation attaches to",
    )
    sann2.add_argument(
        "--user-id", dest="user_id", required=True,
        help="Owner of the alert (also stamped as the author)",
    )
    sann2.add_argument(
        "--body", required=True,
        help='Free-form note ("escalated to ops team")',
    )
    sann2.add_argument("--json", action="store_true")
    sann2.set_defaults(func=_cmd_annotations_add)

    sann3 = sann.add_parser(
        "delete", help="Delete one annotation (author-only)",
    )
    sann3.add_argument("annotation_id")
    sann3.add_argument(
        "--user-id", dest="user_id", required=True,
        help="User scope — must own the alert AND be the author",
    )
    sann3.add_argument("--json", action="store_true")
    sann3.set_defaults(func=_cmd_annotations_delete)

    # ── schedules ─────────────────────────────────────────────────────────
    p_sch = sub.add_parser("schedules", help="Report-schedule subcommands")
    sch_sub = p_sch.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    sch1 = sch_sub.add_parser("list", help="List a user's report schedules")
    sch1.add_argument("--user-id", dest="user_id", required=True)
    sch1.add_argument("--json", action="store_true")
    sch1.set_defaults(func=_cmd_schedules_list)

    sch2 = sch_sub.add_parser("create", help="Create a new schedule")
    sch2.add_argument("--user-id", dest="user_id", required=True)
    sch2.add_argument("--name", required=True, help='Operator-facing label')
    sch2.add_argument("--cron", required=True, help='5-field cron string e.g. "0 9 * * *"')
    sch2.add_argument("--json", action="store_true")
    sch2.set_defaults(func=_cmd_schedules_create)

    sch3 = sch_sub.add_parser("delete", help="Delete one schedule")
    sch3.add_argument("schedule_id")
    sch3.add_argument("--user-id", dest="user_id", required=True)
    sch3.add_argument("--json", action="store_true")
    sch3.set_defaults(func=_cmd_schedules_delete)

    sch4 = sch_sub.add_parser("enable", help="Enable one schedule")
    sch4.add_argument("schedule_id")
    sch4.add_argument("--user-id", dest="user_id", required=True)
    sch4.add_argument("--json", action="store_true")
    sch4.set_defaults(func=_cmd_schedules_enable)

    sch5 = sch_sub.add_parser("disable", help="Disable one schedule")
    sch5.add_argument("schedule_id")
    sch5.add_argument("--user-id", dest="user_id", required=True)
    sch5.add_argument("--json", action="store_true")
    sch5.set_defaults(func=_cmd_schedules_disable)

    sch6 = sch_sub.add_parser("run-once", help="Trigger an immediate manual run")
    sch6.add_argument("schedule_id")
    sch6.add_argument("--user-id", dest="user_id", required=True)
    sch6.add_argument("--json", action="store_true")
    sch6.set_defaults(func=_cmd_schedules_run_once)

    # ── rules ─────────────────────────────────────────────────────────────
    # Config-as-code for alert rules. export / import / diff against the
    # user's persisted rule set. Output of export is YAML by contract;
    # input to import / diff is a YAML file.
    p_ru = sub.add_parser("rules", help="Alert-rule config-as-code subcommands")
    sr_sub = p_ru.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    sru1 = sr_sub.add_parser(
        "export",
        help="Render the user's rules as YAML (stdout, or --out FILE)",
    )
    sru1.add_argument(
        "--user-id", dest="user_id", default=None,
        help="User scope (default: legacy global)",
    )
    sru1.add_argument(
        "--out", default=None,
        help="Write YAML to this path instead of stdout",
    )
    sru1.set_defaults(func=_cmd_rules_export)

    sru2 = sr_sub.add_parser(
        "import",
        help="Parse YAML file and save_rules (overwrites existing set)",
    )
    sru2.add_argument(
        "--in", dest="in_path", required=True,
        help="Path to the YAML file to import",
    )
    sru2.add_argument(
        "--user-id", dest="user_id", default=None,
        help="User scope (default: legacy global)",
    )
    sru2.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Show what would be saved without writing",
    )
    sru2.set_defaults(func=_cmd_rules_import)

    sru3 = sr_sub.add_parser(
        "diff",
        help="Show unified diff between YAML file and the user's current rules",
    )
    sru3.add_argument(
        "--in", dest="in_path", required=True,
        help="Path to the YAML file to compare against",
    )
    sru3.add_argument(
        "--user-id", dest="user_id", default=None,
        help="User scope (default: legacy global)",
    )
    sru3.set_defaults(func=_cmd_rules_diff)

    # CSV variant — same export / import / diff shape as the YAML
    # triplet above, but the wire format is CSV (Excel-friendly,
    # UTF-8 BOM, target_channels joined with '|'). The YAML
    # subcommands stay; CSV adds three new ones.
    sru4 = sr_sub.add_parser(
        "export-csv",
        help="Render the user's rules as CSV (stdout, or --out FILE)",
    )
    sru4.add_argument(
        "--user-id", dest="user_id", default=None,
        help="User scope (default: legacy global)",
    )
    sru4.add_argument(
        "--out", default=None,
        help="Write CSV to this path instead of stdout",
    )
    sru4.set_defaults(func=_cmd_rules_export_csv)

    sru5 = sr_sub.add_parser(
        "import-csv",
        help="Parse CSV file and save_rules (overwrites existing set)",
    )
    sru5.add_argument(
        "--in", dest="in_path", required=True,
        help="Path to the CSV file to import",
    )
    sru5.add_argument(
        "--user-id", dest="user_id", default=None,
        help="User scope (default: legacy global)",
    )
    sru5.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Show what would be saved without writing",
    )
    sru5.set_defaults(func=_cmd_rules_import_csv)

    sru6 = sr_sub.add_parser(
        "diff-csv",
        help="Show unified diff between CSV file and the user's current rules",
    )
    sru6.add_argument(
        "--in", dest="in_path", required=True,
        help="Path to the CSV file to compare against",
    )
    sru6.add_argument(
        "--user-id", dest="user_id", default=None,
        help="User scope (default: legacy global)",
    )
    sru6.set_defaults(func=_cmd_rules_diff_csv)

    # ── escalations ───────────────────────────────────────────────────────
    # Per-rule alert-escalation chains (schema v24). Four subcommands —
    # list / add / delete / clear. Every subcommand is per-user scoped
    # via the ``--user-id`` flag; ``add`` additionally validates the
    # supplied --channel-id against the user's channel set so a
    # mistyped id is caught at CLI time rather than at dispatch time.
    p_esc = sub.add_parser(
        "escalations", help="Alert-escalation chain subcommands",
    )
    s_esc = p_esc.add_subparsers(
        dest="subcommand", required=True, metavar="SUB",
    )

    esc1 = s_esc.add_parser(
        "list", help="List a rule's escalation chain (step ASC)",
    )
    esc1.add_argument(
        "rule_id",
        help="Rule id whose chain to list",
    )
    esc1.add_argument(
        "--user-id", dest="user_id", required=True,
        help="User scope — alice cannot list bob's chain",
    )
    esc1.add_argument("--json", action="store_true")
    esc1.set_defaults(func=_cmd_escalations_list)

    esc2 = s_esc.add_parser(
        "add", help="Persist or replace one step in a chain",
    )
    esc2.add_argument(
        "rule_id",
        help="Rule id the step belongs to",
    )
    esc2.add_argument(
        "--user-id", dest="user_id", required=True,
        help="User scope — chain owner (also the channel owner)",
    )
    esc2.add_argument(
        "--step", required=True, type=int,
        help="1-indexed step number (re-using replaces the existing row)",
    )
    esc2.add_argument(
        "--after-minutes", dest="after_minutes",
        required=True, type=int,
        help="Minutes from the previous step (or alert creation for step 1)",
    )
    esc2.add_argument(
        "--channel-id", dest="channel_id", required=True,
        help="Delivery-channel id (must exist in --user-id's set)",
    )
    esc2.add_argument("--json", action="store_true")
    esc2.set_defaults(func=_cmd_escalations_add)

    esc3 = s_esc.add_parser(
        "delete", help="Delete one step in a chain by chain_id",
    )
    esc3.add_argument("chain_id")
    esc3.add_argument(
        "--user-id", dest="user_id", required=True,
        help="User scope — alice cannot delete bob's chain step",
    )
    esc3.add_argument("--json", action="store_true")
    esc3.set_defaults(func=_cmd_escalations_delete)

    esc4 = s_esc.add_parser(
        "clear", help="Delete every step in a rule's chain",
    )
    esc4.add_argument(
        "rule_id",
        help="Rule id whose chain to wipe",
    )
    esc4.add_argument(
        "--user-id", dest="user_id", required=True,
        help="User scope — alice cannot clear bob's chain",
    )
    esc4.add_argument("--json", action="store_true")
    esc4.set_defaults(func=_cmd_escalations_clear)

    # ── digest (weekly summary) ────────────────────────────────────────
    # Five subcommands cover the operator surface for the per-user
    # weekly digest: render a preview without dispatching, send it now
    # outside the schedule, opt a user in, opt them out, and inspect
    # the persisted config.
    p_dg = sub.add_parser("digest", help="Weekly-digest subcommands")
    sdg = p_dg.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    dg1 = sdg.add_parser("preview", help="Render this week's digest as markdown")
    dg1.add_argument("--user-id", required=True)
    dg1.add_argument(
        "--week-start",
        default=None,
        help="ISO date inside the target week (defaults to this week)",
    )
    dg1.add_argument("--json", action="store_true")
    dg1.set_defaults(func=_cmd_digest_preview)

    dg2 = sdg.add_parser(
        "send-now",
        help="Dispatch the digest immediately (bypasses the schedule)",
    )
    dg2.add_argument("--user-id", required=True)
    dg2.add_argument("--json", action="store_true")
    dg2.set_defaults(func=_cmd_digest_send_now)

    dg3 = sdg.add_parser("enable", help="Enable the weekly digest for a user")
    dg3.add_argument("--user-id", required=True)
    dg3.add_argument(
        "--channels",
        required=True,
        help="Comma-separated list of channel_ids to dispatch to",
    )
    dg3.add_argument("--day-of-week", default="monday")
    dg3.add_argument("--hour", type=int, default=14)
    dg3.add_argument("--json", action="store_true")
    dg3.set_defaults(func=_cmd_digest_enable)

    dg4 = sdg.add_parser("disable", help="Disable the weekly digest for a user")
    dg4.add_argument("--user-id", required=True)
    dg4.add_argument("--json", action="store_true")
    dg4.set_defaults(func=_cmd_digest_disable)

    dg5 = sdg.add_parser("config", help="Show the user's current digest config")
    dg5.add_argument("--user-id", required=True)
    dg5.add_argument("--json", action="store_true")
    dg5.set_defaults(func=_cmd_digest_config)

    # ── retries (delivery_retry_queue, v26) ────────────────────────────
    # Five subcommands cover the operator surface for the persistent
    # retry queue. ``list`` is the read; ``cancel`` + ``manual`` are
    # per-row mutators; ``cleanup`` triggers the retention prune;
    # ``process`` runs a synchronous worker pass for ad-hoc draining.
    p_rt = sub.add_parser("retries", help="Delivery-retry-queue subcommands")
    srt = p_rt.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    rt1 = srt.add_parser(
        "list",
        help="List retry rows by status (default: pending)",
    )
    rt1.add_argument(
        "--status",
        choices=["pending", "failed", "succeeded"],
        default="pending",
    )
    rt1.add_argument(
        "--user-id", default="",
        help="Scope to a user_id ('' = every user)",
    )
    rt1.add_argument("--limit", type=int, default=100)
    rt1.add_argument("--json", action="store_true")
    rt1.set_defaults(func=_cmd_retries_list)

    rt2 = srt.add_parser(
        "cancel",
        help="Cancel a pending retry (marks it failed with reason)",
    )
    rt2.add_argument("queue_id")
    rt2.add_argument("--user-id", required=True)
    rt2.add_argument("--json", action="store_true")
    rt2.set_defaults(func=_cmd_retries_cancel)

    rt3 = srt.add_parser(
        "manual",
        help="Set next_attempt_at=NOW so the next worker pass picks it up",
    )
    rt3.add_argument("queue_id")
    rt3.add_argument("--user-id", required=True)
    rt3.add_argument("--json", action="store_true")
    rt3.set_defaults(func=_cmd_retries_manual)

    rt4 = srt.add_parser(
        "cleanup",
        help="Delete completed rows older than --retention-days",
    )
    rt4.add_argument(
        "--retention-days",
        type=int, default=14,
        help="Delete succeeded/failed rows older than N days (default 14)",
    )
    rt4.add_argument("--json", action="store_true")
    rt4.set_defaults(func=_cmd_retries_cleanup)

    rt5 = srt.add_parser(
        "process",
        help="Run one retry pass NOW (synchronous)",
    )
    rt5.add_argument("--json", action="store_true")
    rt5.set_defaults(func=_cmd_retries_process)

    # ── calendar (ICS feed subscription tokens) ──────────────────────────
    # Four subcommands: token-show / token-generate / token-revoke and
    # an export helper that writes the ICS body to a file (offline
    # one-shot import). The subscription URL is the secret; the
    # token-generate output is the ONLY place the raw value is
    # surfaced so an operator who loses it has to regenerate.
    p_cal = sub.add_parser("calendar", help="Calendar (ICS) subcommands")
    s_cal = p_cal.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    cal1 = s_cal.add_parser(
        "token-show",
        help="Show the user's current calendar subscription token",
    )
    cal1.add_argument("--user-id", required=True)
    cal1.add_argument("--json", action="store_true")
    cal1.set_defaults(func=_cmd_calendar_token_show)

    cal2 = s_cal.add_parser(
        "token-generate",
        help="Generate (or rotate) the user's calendar subscription token",
    )
    cal2.add_argument("--user-id", required=True)
    cal2.add_argument("--json", action="store_true")
    cal2.set_defaults(func=_cmd_calendar_token_generate)

    cal3 = s_cal.add_parser(
        "token-revoke",
        help="Clear the saved calendar subscription token for a user",
    )
    cal3.add_argument("--user-id", required=True)
    cal3.add_argument("--json", action="store_true")
    cal3.set_defaults(func=_cmd_calendar_token_revoke)

    cal4 = s_cal.add_parser(
        "export",
        help="Render the user's incident feed to an .ics file or stdout",
    )
    cal4.add_argument("--user-id", required=True)
    cal4.add_argument("--window", type=int, default=30)
    cal4.add_argument(
        "--out", default=None,
        help="Write the .ics body to this file path instead of stdout",
    )
    cal4.add_argument("--json", action="store_true")
    cal4.set_defaults(func=_cmd_calendar_export)

    # ── disruption ──────────────────────────────────────────────────────────
    # Backtest the SSI against historical events; print template-based "why
    # this route is stressed" explanations.
    p_dis = sub.add_parser("disruption", help="Disruption-Alpha subcommands")
    s_dis = p_dis.add_subparsers(dest="subcommand", required=True, metavar="SUB")

    dis1 = s_dis.add_parser(
        "backtest",
        help="Backtest the SSI against historical disruption events",
    )
    dis1.add_argument(
        "event_id",
        nargs="?",
        default=None,
        help="Optional: backtest one event (e.g. suez_2021). Omit to run all.",
    )
    dis1.add_argument(
        "--threshold",
        default="Stressed",
        choices=["Calm", "Elevated", "Stressed", "Severe"],
        help="SSI band at which an event counts as detected (default Stressed)",
    )
    dis1.add_argument(
        "--window",
        type=int,
        default=14,
        help="Evaluation window (days) for the lead-time cap (default 14)",
    )
    dis1.add_argument("--json", action="store_true")
    dis1.set_defaults(func=_cmd_disruption_backtest)

    dis2 = s_dis.add_parser(
        "explain-routes",
        help="Print top-N route stress explanations",
    )
    dis2.add_argument(
        "--top",
        type=int,
        default=5,
        help="Number of routes to explain (default 5)",
    )
    dis2.add_argument("--json", action="store_true")
    dis2.set_defaults(func=_cmd_disruption_explain_routes)

    return p


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    """Parse ``argv`` and dispatch to the matching handler.

    Returns the desired process exit code:
      * ``0`` — handler ran cleanly
      * ``1`` — handler raised; the message was printed to stderr
      * ``2`` — argparse rejected the invocation

    Tests call this directly with a synthetic ``argv``; the ``__main__``
    block at the bottom of the file calls it with ``sys.argv[1:]``.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse raises SystemExit on bad input. Normalise the code so
        # tests can assert "2 means usage error" without depending on the
        # platform default. ``exc.code`` is ``None`` for ``--help``,
        # ``int`` (usually 2) otherwise.
        code = exc.code if isinstance(exc.code, int) else 0
        return code

    handler: Optional[Callable[[argparse.Namespace], None]] = getattr(args, "func", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2

    try:
        handler(args)
        return 0
    except SystemExit as exc:
        # Handlers (e.g. ``_cmd_users_create``, ``_cmd_channels_set_budget``)
        # raise ``SystemExit(N)`` to surface a non-zero exit code without
        # propagating a traceback. Normalise the code so tests can call
        # ``main()`` directly without the interpreter actually exiting.
        return exc.code if isinstance(exc.code, int) else 0
    except Exception as exc:  # noqa: BLE001 — top-level guard by contract
        # Single-line stderr message so a calling shell can capture
        # ``2>&1 | tail -1`` without scrolling through a traceback. The
        # CLI must NEVER let an exception escape to the shell.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
