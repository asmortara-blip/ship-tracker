"""``python -m tools.schema_docs_cli`` - generate DB schema documentation.

Three subcommands wrap ``tools.schema_docs``:

* ``markdown`` - introspect the live DB and render the schema as
  Markdown. Default output path is ``docs/SCHEMA.md`` so a freshly-run
  invocation drops the doc straight into the docs tree.
* ``json`` - emit the introspected dict as JSON. Default destination
  is stdout (so the output can be piped into ``jq``); ``--out PATH``
  redirects to a file.
* ``history`` - parse ``state/migrations.py`` via the stdlib ``ast``
  module and render the per-version history as Markdown. Default
  output path is ``docs/SCHEMA_HISTORY.md``.

This CLI is kept SEPARATE from ``tools.ops_cli`` on purpose - that one
is already large and operator-focused (token rotation, MFA enrolment,
audit export, …). Schema docs are a developer-tooling concern and
deserve their own entry point so ``--help`` stays scannable.

Exit codes
----------
* ``0`` - the requested artifact was produced successfully.
* ``1`` - the requested artifact could not be produced (DB missing,
  output path unwritable, …). The error is printed to stderr; stdout
  is left empty so piped JSON consumers still get clean output.
* ``2`` - argparse rejected the invocation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


# Anchor relative-path defaults at the project root so the CLI behaves
# the same regardless of CWD.
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# Default output destinations. The user can override with --out.
_DEFAULT_SCHEMA_MD: Path = _PROJECT_ROOT / "docs" / "SCHEMA.md"
_DEFAULT_HISTORY_MD: Path = _PROJECT_ROOT / "docs" / "SCHEMA_HISTORY.md"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.schema_docs_cli",
        description=(
            "Generate Markdown / JSON documentation of the SQLite "
            "schema from a live DB + migration history."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    # markdown subcommand
    p_md = sub.add_parser(
        "markdown",
        help=(
            "Introspect the live DB and write a Markdown schema doc. "
            "Default output: docs/SCHEMA.md."
        ),
    )
    p_md.add_argument(
        "--out",
        default=None,
        help=f"Output file path (default: {_DEFAULT_SCHEMA_MD}).",
    )
    p_md.add_argument(
        "--db",
        default=None,
        help=(
            "Path to a specific .db file. Default: live "
            "cache/ship_tracker.db via state.db.DB_PATH."
        ),
    )

    # json subcommand
    p_json = sub.add_parser(
        "json",
        help=(
            "Emit the introspected schema as JSON (machine-readable). "
            "Default destination: stdout."
        ),
    )
    p_json.add_argument(
        "--out",
        default=None,
        help="Output file path (default: stdout).",
    )
    p_json.add_argument(
        "--db",
        default=None,
        help="Path to a specific .db file. Default: live DB via state.db.DB_PATH.",
    )

    # history subcommand
    p_hist = sub.add_parser(
        "history",
        help=(
            "Parse state/migrations.py and render the per-version "
            "history as Markdown. Default output: docs/SCHEMA_HISTORY.md."
        ),
    )
    p_hist.add_argument(
        "--out",
        default=None,
        help=f"Output file path (default: {_DEFAULT_HISTORY_MD}).",
    )

    return p


def _write_text(text: str, out: Optional[Path]) -> int:
    """Write ``text`` to ``out`` (creating parents) or to stdout when
    ``out`` is None. Returns the exit code (0 success, 1 on write
    failure)."""
    if out is None:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        return 0
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to write {out}: {exc}", file=sys.stderr)
        return 1


def _cmd_markdown(args: argparse.Namespace) -> int:
    from tools.schema_docs import introspect_schema, render_schema_markdown
    db_path = Path(args.db) if args.db else None
    schema = introspect_schema(db_path=db_path)
    text = render_schema_markdown(schema)
    out = Path(args.out) if args.out else _DEFAULT_SCHEMA_MD
    rc = _write_text(text, out)
    if rc == 0 and out is not None:
        # Mirror the backup_cli pattern - a short success line so the
        # operator running this by hand sees confirmation.
        print(f"wrote {out}", file=sys.stderr)
    # Surface the introspection error (if any) as a non-zero exit so a
    # scripted invocation can catch it. The Markdown still got written
    # (it carries the error inline) - this is informational only.
    if isinstance(schema, dict) and schema.get("error"):
        print(f"warning: {schema['error']}", file=sys.stderr)
        return 1
    return rc


def _cmd_json(args: argparse.Namespace) -> int:
    from tools.schema_docs import introspect_schema
    db_path = Path(args.db) if args.db else None
    schema = introspect_schema(db_path=db_path)
    text = json.dumps(schema, indent=2, sort_keys=True, default=str)
    out = Path(args.out) if args.out else None
    rc = _write_text(text, out)
    if isinstance(schema, dict) and schema.get("error"):
        print(f"warning: {schema['error']}", file=sys.stderr)
        return 1
    return rc


def _cmd_history(args: argparse.Namespace) -> int:
    from tools.schema_docs import (
        render_history_markdown,
        schema_history_from_migrations,
    )
    entries = schema_history_from_migrations()
    text = render_history_markdown(entries)
    out = Path(args.out) if args.out else _DEFAULT_HISTORY_MD
    rc = _write_text(text, out)
    if rc == 0 and out is not None:
        print(f"wrote {out} ({len(entries)} migrations)", file=sys.stderr)
    return rc


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
        return code

    if args.command == "markdown":
        return _cmd_markdown(args)
    if args.command == "json":
        return _cmd_json(args)
    if args.command == "history":
        return _cmd_history(args)

    # argparse with required=True already rejects unknown commands;
    # this branch is unreachable but kept defensive.
    parser.print_help(file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
