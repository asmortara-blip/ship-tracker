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

    secret = generate_secret()
    uri = provisioning_uri(secret, account=account)
    # v21 signature change: enable_mfa returns (ok, recovery_codes).
    # The recovery codes are surfaced to the operator EXACTLY ONCE
    # on stdout — never logged, never re-derivable from the DB. The
    # raw secret is similarly shown once.
    ok, recovery_codes = enable_mfa(args.user_id, secret)
    if not ok:
        raise RuntimeError(
            f"enable_mfa failed for user_id={args.user_id!r} — "
            "unknown user or DB error"
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
