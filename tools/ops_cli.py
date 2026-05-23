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
            "created_at":    u.created_at,
            "last_login_at": u.last_login_at or "(never)",
        }
        for u in users
    ]
    _print_table(rows, columns=["user_id", "username", "role", "created_at", "last_login_at"])


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
            "revoked":      "Y" if t.revoked else "N",
        }
        for t in tokens
    ]
    _print_table(rows, columns=["token_id", "label", "prefix", "created_at", "last_used_at", "revoked"])


def _cmd_tokens_create(args: argparse.Namespace) -> None:
    from auth.tokens import create_token

    result = create_token(args.user_id, args.label)
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
    except Exception as exc:  # noqa: BLE001 — top-level guard by contract
        # Single-line stderr message so a calling shell can capture
        # ``2>&1 | tail -1`` without scrolling through a traceback. The
        # CLI must NEVER let an exception escape to the shell.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
