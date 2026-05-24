"""``python -m tools.anonymize_cli`` — operator-facing CLI for the DB
anonymizer.

Usage
-----

    python -m tools.anonymize_cli --source PATH --output PATH \\
        [--profile standard|aggressive|redact-only] [--verbose]

    python -m tools.anonymize_cli --source PATH --dry-run \\
        [--profile standard|aggressive|redact-only]

Exit codes
----------

* 0 — success.
* 1 — handler error (the message went to stderr).
* 2 — argparse rejected the invocation (missing required flag, etc.).

Examples
--------

Share a dev DB with a teammate:

    python -m tools.anonymize_cli --source cache/ship_tracker.db \\
        --output ~/share/ship_dev.db --verbose

Populate staging from prod:

    python -m tools.anonymize_cli --source /var/lib/ship/prod.db \\
        --output /var/lib/ship/staging.db --profile standard

Hand a QA contractor a shape-faithful skeleton:

    python -m tools.anonymize_cli --source cache/ship_tracker.db \\
        --output /tmp/qa.db --profile redact-only

Preview what would change without writing:

    python -m tools.anonymize_cli --source cache/ship_tracker.db \\
        --dry-run --verbose
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.anonymize_cli",
        description=(
            "Anonymize a copy of the SQLite state DB so it's safe to "
            "share with a teammate, populate staging, or hand to QA."
        ),
    )
    p.add_argument(
        "--source",
        required=True,
        help="Path to the source SQLite DB (opened READ-ONLY).",
    )
    p.add_argument(
        "--output",
        required=False,
        default=None,
        help=(
            "Path where the anonymized copy is written. Required "
            "unless --dry-run is set."
        ),
    )
    p.add_argument(
        "--profile",
        choices=("standard", "aggressive", "redact-only"),
        default="standard",
        help=(
            "Anonymization profile. 'standard' scrubs PII + secrets but "
            "keeps alert/report shape. 'aggressive' also empties "
            "annotation bodies + audit detail_json. 'redact-only' "
            "replaces strings but preserves row counts. Default: standard."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Show what would change without writing the output file. "
            "Source DB is opened READ-ONLY; no mutation happens."
        ),
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-table scrubbed/dropped counts to stderr.",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    """Parse ``argv`` and run the anonymizer. Returns exit code per the
    contract above."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
        return code

    # --output is required unless --dry-run.
    if not args.dry_run and not args.output:
        print(
            "error: --output is required unless --dry-run is set",
            file=sys.stderr,
        )
        return 2

    # Import here so plain `--help` doesn't pay the import cost.
    from tools.db_anonymize import anonymize_db

    try:
        anonymize_db(
            source_path=Path(args.source),
            output_path=Path(args.output) if args.output else Path(args.source),
            profile=args.profile,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        return 0
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — operator-facing top-level guard
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
