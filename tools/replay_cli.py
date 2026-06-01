"""``python -m tools.replay_cli`` — replay historical alerts to a channel.

The operator-facing UI surfaces a "Replay alert" button on the alert
table, but the same action is sometimes needed from a shell. Two
recurring use cases:

* **Channel config validation.** An operator just rewired a Slack
  webhook / swapped a PagerDuty key / added an SMTP recipient. They
  want to verify the channel works against REAL alerts they remember
  (last week's BDI spike, last month's port shutdown), not the
  synthetic ``send_test_ping`` payload.
* **Channel migration testing.** Two channels point at the same
  destination during a migration — operator replays last week's
  CRITICAL fires against the new channel to confirm the new wire
  works identically before deleting the old channel.

This module is intentionally tiny — every handler is a thin wrapper
around :mod:`engine.alert_replay`. The reason it's split from
:mod:`tools.ops_cli` rather than added as a subcommand there is that
``replay`` carries operator risk (sends real messages to real Slack
channels) and we want it isolated from the bulk-administrative ops
verbs so a typo at the keyboard cannot fire a replay by accident.

Output modes
------------
``--json`` produces JSON the stdlib can round-trip. Default output is
a small ASCII table — one row per replay result.

Exit codes
----------
* ``0`` — every replay succeeded (or no work was requested)
* ``1`` — handler raised; the message went to stderr
* ``2`` — argparse rejected the invocation
* ``3`` — handler ran cleanly but at least one individual replay failed

The CLI MUST NEVER bubble an exception out to the shell. Tests rely
on this contract.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
#  Output helpers
# ─────────────────────────────────────────────────────────────────────────────

def _print_json(payload) -> None:
    """Same shape as tools.ops_cli._print_json — JSON to stdout."""
    print(json.dumps(payload, indent=2, default=str))


def _print_table(results: list) -> None:
    """Render a list of ReplayResult dicts as a fixed-width table.

    Empty input prints "(no rows)" so a pipe consumer can tell apart
    "empty result" from "command crashed" — same contract as the
    ops_cli table helper.
    """
    if not results:
        print("(no rows)")
        return
    cols = ["alert_id", "channel_id", "success", "message"]
    rows = [
        {
            "alert_id":   (r["alert_id"][:12] + "…") if len(r["alert_id"]) > 13 else r["alert_id"],
            "channel_id": (r["channel_id"][:12] + "…") if len(r["channel_id"]) > 13 else r["channel_id"],
            "success":    "Y" if r["success"] else "N",
            "message":    (r["message"][:80] + "…") if len(r["message"]) > 81 else r["message"],
        }
        for r in results
    ]
    widths = {
        c: max(len(str(c)), max(len(str(r.get(c, ""))) for r in rows))
        for c in cols
    }
    print("  ".join(str(c).ljust(widths[c]) for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))


def _results_to_dicts(results) -> list[dict]:
    """Convert a list of ReplayResult dataclasses to plain dicts."""
    out = []
    for r in results:
        if r is None:
            continue
        try:
            out.append(asdict(r))
        except Exception:  # noqa: BLE001 — be lenient with hand-rolled stubs
            out.append({
                "alert_id":   getattr(r, "alert_id", ""),
                "channel_id": getattr(r, "channel_id", ""),
                "success":    bool(getattr(r, "success", False)),
                "message":    getattr(r, "message", ""),
            })
    return out


def _exit_code_for(results: list) -> int:
    """Return 0 if every replay succeeded (or list is empty), 3 otherwise.

    Failures bubble up as a distinct exit code so an automated wrapper
    (e.g. a Makefile rule that asserts "all replays delivered") can
    pin the boundary without parsing stdout. We deliberately do NOT
    use exit 1 for a per-replay failure: exit 1 is reserved for
    handler-level exceptions in this CLI family, and "the dispatch
    refused us cleanly" is a domain outcome, not a programming error.
    """
    if not results:
        return 0
    return 0 if all(r.get("success") for r in results) else 3


# ─────────────────────────────────────────────────────────────────────────────
#  Subcommand handlers
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_replay(args: argparse.Namespace) -> int:
    """Replay a single alert to one channel."""
    from engine.alert_replay import replay_alert

    result = replay_alert(args.alert_id, args.channel_id, user_id=args.user_id)
    dicts = _results_to_dicts([result])
    if args.json:
        _print_json(dicts[0] if dicts else {})
    else:
        _print_table(dicts)
    return _exit_code_for(dicts)


def _cmd_bulk(args: argparse.Namespace) -> int:
    """Replay alerts matching the filter to one channel."""
    from engine.alert_replay import (
        DEFAULT_REPLAY_LIMIT,
        parse_relative_since,
        replay_alerts_by_filter,
    )

    since_iso: Optional[str] = None
    if args.since:
        since_iso = parse_relative_since(args.since)
        if since_iso is None:
            # Tolerate the malformed input — print a one-line note to
            # stderr so the operator notices, but proceed without the
            # since filter so a typo doesn't abort the whole command.
            print(
                f"warning: could not parse --since {args.since!r}; "
                f"ignoring (expected e.g. '7d', '24h', '30m')",
                file=sys.stderr,
            )

    results = replay_alerts_by_filter(
        channel_id=args.channel_id,
        user_id=args.user_id,
        severity=args.severity,
        alert_type=args.alert_type,
        since=since_iso,
        until=None,
        limit=args.limit if args.limit is not None else DEFAULT_REPLAY_LIMIT,
    )
    dicts = _results_to_dicts(results)
    if args.json:
        _print_json({
            "count":       len(dicts),
            "succeeded":   sum(1 for r in dicts if r["success"]),
            "failed":      sum(1 for r in dicts if not r["success"]),
            "results":     dicts,
        })
    else:
        _print_table(dicts)
        if dicts:
            print(
                f"\n{sum(1 for r in dicts if r['success'])}/{len(dicts)} succeeded"
            )
    return _exit_code_for(dicts)


# ─────────────────────────────────────────────────────────────────────────────
#  Parser + main
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level parser + every subparser.

    Mirrors the style used by ``tools.ops_cli._build_parser`` — fresh
    parser per call so tests can build a sandbox parser without polluting
    module state.
    """
    p = argparse.ArgumentParser(
        prog="python -m tools.replay_cli",
        description="Replay historical alerts to a delivery channel "
                    "without consuming budget.",
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # ── replay ────────────────────────────────────────────────────────────
    r = sub.add_parser(
        "replay",
        help="Replay one alert to one channel",
    )
    r.add_argument("alert_id", help="Alert id to replay")
    r.add_argument(
        "--channel-id", required=True, dest="channel_id",
        help="Delivery channel id to dispatch to",
    )
    r.add_argument(
        "--user-id", required=True, dest="user_id",
        help="Scope to a user_id (alert + channel must both belong "
             "to this user)",
    )
    r.add_argument("--json", action="store_true", help="JSON output")
    r.set_defaults(func=_cmd_replay)

    # ── bulk ──────────────────────────────────────────────────────────────
    b = sub.add_parser(
        "bulk",
        help="Replay every alert matching the filter to one channel",
    )
    b.add_argument(
        "--channel-id", required=True, dest="channel_id",
        help="Delivery channel id to dispatch to",
    )
    b.add_argument(
        "--user-id", required=True, dest="user_id",
        help="Scope to a user_id (alerts + channel must both belong "
             "to this user)",
    )
    b.add_argument(
        "--severity",
        choices=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
        default=None,
        help="Filter to one severity (default: any)",
    )
    b.add_argument(
        "--alert-type", dest="alert_type", default=None,
        help="Filter to one alert_type (e.g. BDI_MOVE, CONGESTION). "
             "Default: any",
    )
    b.add_argument(
        "--since", default=None,
        help="Replay alerts newer than this relative duration "
             "(e.g. '7d', '24h', '30m'). Default: no lower bound",
    )
    b.add_argument(
        "--limit", type=int, default=50,
        help="Max alerts to replay (default 50, hard cap 200)",
    )
    b.add_argument("--json", action="store_true", help="JSON output")
    b.set_defaults(func=_cmd_bulk)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    """Parse ``argv`` and dispatch.

    Returns the desired process exit code:
      * ``0`` — every replay succeeded (or no work was requested)
      * ``1`` — handler raised; message went to stderr
      * ``2`` — argparse rejected the invocation
      * ``3`` — at least one replay failed cleanly

    Tests call this directly with a synthetic ``argv``; the ``__main__``
    block at the bottom of the file calls it with ``sys.argv[1:]``.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse raises SystemExit on bad input. Normalise the code so
        # tests can assert "2 means usage error" without depending on the
        # platform default.
        code = exc.code if isinstance(exc.code, int) else 0
        return code

    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2

    try:
        return int(handler(args) or 0)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    except Exception as exc:  # noqa: BLE001 — top-level guard by contract
        # Single-line stderr message so a calling shell can capture
        # ``2>&1 | tail -1`` without scrolling through a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
