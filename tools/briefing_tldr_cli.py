"""tools/briefing_tldr_cli.py — print the day's briefing TLDR to stdout.

Reads the per-UTC-day cached ``DailyNarration`` (primed by the worker's
``run_briefing_tldr_job`` or the first viewer of the Briefing tab),
distills it via ``engine.daily_briefing_tldr.generate_tldr`` (a cache hit
once the day is primed — no extra Claude call), and prints it so an
operator or script can pipe it to SMS / Slack / email.

Usage::

    # The day's TLDR as a tight paragraph (SMS/Slack-ready).
    python -m tools.briefing_tldr_cli

    # A specific UTC day.
    python -m tools.briefing_tldr_cli --date 2026-05-29

    # JSON for a downstream dashboard, or the email-ready HTML / subject.
    python -m tools.briefing_tldr_cli --format json
    python -m tools.briefing_tldr_cli --format html
    python -m tools.briefing_tldr_cli --format subject

    # Bypass the TLDR day-cache (force a fresh distillation if a key is set).
    python -m tools.briefing_tldr_cli --refresh

Exit codes: 0 on success; 1 on a bad ``--date`` or when no briefing has
been narrated for that day yet (open the Briefing tab or run the worker).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone

from delivery.briefing_tldr import build_subject_line, render_html
from engine.daily_briefing_tldr import generate_tldr
from engine.narration_engine import (
    NARRATION_CACHE_DIR,
    _narration_cache_path,
    _read_narration_cache,
)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser. Exposed so tools.cli_index can introspect it."""
    parser = argparse.ArgumentParser(
        prog="tools.briefing_tldr_cli",
        description=(
            "Print the day's one-paragraph shipping-briefing TLDR (from the "
            "day-cached narration) for piping to SMS / Slack / email."
        ),
    )
    parser.add_argument(
        "--date", default=None,
        help=(
            "UTC day to summarise as ISO YYYY-MM-DD (default: today UTC). "
            "Reads the narration cached for that day."
        ),
    )
    parser.add_argument(
        "--format", "-f",
        choices=("text", "json", "html", "subject"), default="text",
        help=(
            "Output: 'text' = the raw TLDR paragraph (default, SMS/Slack-"
            "ready); 'json' = structured fields; 'html' = email-ready lede; "
            "'subject' = a short subject line."
        ),
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Bypass the TLDR day-cache and re-distill (uses a key if set).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.date:
        try:
            target = date.fromisoformat(args.date)
        except ValueError:
            print(
                f"error: bad --date {args.date!r}: expected YYYY-MM-DD",
                file=sys.stderr,
            )
            return 1
    else:
        target = datetime.now(timezone.utc).date()

    narration = _read_narration_cache(
        _narration_cache_path(target, NARRATION_CACHE_DIR)
    )
    if narration is None:
        print(
            f"error: no briefing narrated for {target.isoformat()} yet — "
            "open the Briefing tab or run the daily worker first.",
            file=sys.stderr,
        )
        return 1

    summary = generate_tldr(narration, use_cache=not args.refresh)
    date_iso = target.isoformat()

    if args.format == "json":
        print(json.dumps({
            "date":         date_iso,
            "text":         summary.text,
            "source":       summary.source,
            "model":        summary.model,
            "tokens_in":    summary.tokens_in,
            "tokens_out":   summary.tokens_out,
            "generated_at": summary.generated_at,
        }, indent=2, default=str))
    elif args.format == "html":
        print(render_html(summary, date_iso))
    elif args.format == "subject":
        print(build_subject_line(summary, date_iso))
    else:  # text — the raw paragraph, tightest form for piping
        print(summary.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
