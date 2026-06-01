"""``python -m tools.completion_cli`` — generate bash / zsh tab-completion
scripts for the project's argparse CLIs.

Wraps :mod:`tools.completion_gen` with a small CLI so operators can
regenerate the committed completion files in ``docs/completion/`` with
one command::

    python -m tools.completion_cli all --out-dir docs/completion

The ``all`` subcommand iterates over every registered CLI module
(``ops_cli``, ``backup_cli``, ``replay_cli``, …) and writes a bash + zsh
file per CLI plus a single ``INSTALL.md`` with shell-specific install
hints.

The individual ``bash`` / ``zsh`` subcommands let you regenerate a single
target on stdout (useful when wiring this into a make rule or comparing
against the committed version).

Exit codes
----------
* ``0`` — success
* ``1`` — handler raised; the message went to stderr
* ``2`` — argparse rejected the invocation
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Callable, Optional

from tools.completion_gen import render_bash_completion, render_zsh_completion


# Registry of CLIs we know how to introspect. Each entry maps a stable
# program name (used as the install/file basename) to the dotted module
# whose ``_build_parser`` factory we walk.
#
# NOTE: keep this list in sync with new CLI modules under ``tools/``.
# Adding a CLI is a one-line append here — the renderer + CLI handlers
# don't need to know the program name in advance.
KNOWN_CLIS: dict[str, str] = {
    "ops_cli": "tools.ops_cli",
    "backup_cli": "tools.backup_cli",
    "replay_cli": "tools.replay_cli",
    "db_check_cli": "tools.db_check_cli",
    "anonymize_cli": "tools.anonymize_cli",
    "changelog_cli": "tools.changelog_cli",
    "openapi_cli": "tools.openapi_cli",
    "schema_docs_cli": "tools.schema_docs_cli",
}


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _load_parser(module_name: str) -> argparse.ArgumentParser:
    """Import ``module_name`` and call its ``_build_parser`` factory.

    Each ops/* CLI module in this project follows the same convention —
    a top-level ``_build_parser()`` returning an ``ArgumentParser``. We
    intentionally do NOT call ``main()`` here; the parser must be safe
    to introspect without executing handlers.
    """
    module = importlib.import_module(module_name)
    builder = getattr(module, "_build_parser", None)
    if builder is None:
        raise RuntimeError(
            f"Module {module_name!r} has no ``_build_parser`` factory. "
            f"Completion generation only supports parser-factory CLIs."
        )
    return builder()


def _write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` (creating parent dirs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ─────────────────────────────────────────────────────────────────────────────
#  Subcommand handlers
# ─────────────────────────────────────────────────────────────────────────────


def _cmd_bash(args: argparse.Namespace) -> None:
    """Render bash completion for ``--program`` to stdout / ``--out``."""
    program = args.program
    module = KNOWN_CLIS.get(program, args.module)
    if module is None:
        raise RuntimeError(
            f"Unknown program {program!r}. Pass --module dotted.path to "
            f"point at the module exposing ``_build_parser``, or pick one "
            f"of: {', '.join(sorted(KNOWN_CLIS))}."
        )
    parser = _load_parser(module)
    script = render_bash_completion(parser, program_name=program)
    if args.out:
        _write(Path(args.out), script)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(script)


def _cmd_zsh(args: argparse.Namespace) -> None:
    """Render zsh completion for ``--program`` to stdout / ``--out``."""
    program = args.program
    module = KNOWN_CLIS.get(program, args.module)
    if module is None:
        raise RuntimeError(
            f"Unknown program {program!r}. Pass --module dotted.path to "
            f"point at the module exposing ``_build_parser``, or pick one "
            f"of: {', '.join(sorted(KNOWN_CLIS))}."
        )
    parser = _load_parser(module)
    script = render_zsh_completion(parser, program_name=program)
    if args.out:
        _write(Path(args.out), script)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(script)


def _cmd_all(args: argparse.Namespace) -> None:
    """Regenerate bash + zsh completion for every registered CLI.

    Writes one ``<program>.bash`` + one ``_<program>`` per known CLI
    under ``--out-dir`` and a single ``INSTALL.md`` with shell-specific
    install hints. Skips CLIs that fail to import (e.g. missing optional
    deps) and reports the skip on stderr so CI can still complete a
    partial regeneration without exiting non-zero.
    """
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    skipped: list[tuple[str, str]] = []
    for program, module in sorted(KNOWN_CLIS.items()):
        try:
            parser = _load_parser(module)
        except Exception as exc:  # noqa: BLE001 — per-CLI guard
            skipped.append((program, str(exc)))
            print(f"  skip  {program}: {exc}", file=sys.stderr)
            continue

        bash_path = out_dir / f"{program}.bash"
        zsh_path = out_dir / f"_{program}"
        _write(bash_path, render_bash_completion(parser, program_name=program))
        _write(zsh_path, render_zsh_completion(parser, program_name=program))
        written.append(program)
        print(f"  wrote {bash_path}", file=sys.stderr)
        print(f"  wrote {zsh_path}", file=sys.stderr)

    # Emit a tiny install reference that matches the docs in DEPLOYMENT.md.
    install_md = _render_install_md(written)
    _write(out_dir / "INSTALL.md", install_md)
    print(f"  wrote {out_dir / 'INSTALL.md'}", file=sys.stderr)

    print(
        f"generated completion for {len(written)} CLI(s); "
        f"skipped {len(skipped)}",
        file=sys.stderr,
    )


def _render_install_md(programs: list[str]) -> str:
    """Build the per-shell install snippet committed alongside the
    completion files. Mirrors the section added to DEPLOYMENT.md."""
    lines = [
        "# Tab-completion install",
        "",
        "Auto-generated. Regenerate via:",
        "",
        "```bash",
        "python -m tools.completion_cli all --out-dir docs/completion",
        "```",
        "",
        "## bash",
        "",
        "```bash",
    ]
    for p in programs:
        lines.append(f"source docs/completion/{p}.bash")
    lines.extend(
        [
            "",
            "# Or persist for all users on the host:",
        ]
    )
    for p in programs:
        lines.append(f"sudo cp docs/completion/{p}.bash /etc/bash_completion.d/")
    lines.extend(
        [
            "```",
            "",
            "## zsh",
            "",
            "```bash",
            "# Add docs/completion to your $fpath, then autoload + compinit:",
            "fpath=(\"$PWD/docs/completion\" $fpath)",
        ]
    )
    for p in programs:
        lines.append(f"autoload -U _{p}")
    lines.extend(
        [
            "compinit",
            "```",
            "",
            "## Notes",
            "",
            "* Completion only covers subcommand names — option *values* are",
            "  not completed (operators just type the id).",
            "* If you run a CLI via ``python -m tools.ops_cli`` the bash",
            "  ``complete -F`` hook binds to argv[0] (``python``), so the",
            "  hook won't fire. Wrap the invocation in a shell function or",
            "  drop a ``ops_cli`` wrapper script on ``$PATH`` to use",
            "  completion in that mode.",
            "",
        ]
    )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  Parser + entry point
# ─────────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level parser + every subparser.

    Mirrors the factory style in ``tools.ops_cli._build_parser`` so the
    completion-CLI itself can be a target of completion generation (it
    appears in ``KNOWN_CLIS`` if you choose to add it).
    """
    p = argparse.ArgumentParser(
        prog="python -m tools.completion_cli",
        description=(
            "Generate bash / zsh tab-completion scripts for the project's "
            "argparse-based CLIs."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # ── bash ──────────────────────────────────────────────────────────────
    pb = sub.add_parser("bash", help="Render bash completion for one CLI")
    pb.add_argument(
        "--program",
        default="ops_cli",
        help=f"Program name (default: ops_cli). Known: {', '.join(sorted(KNOWN_CLIS))}",
    )
    pb.add_argument(
        "--module",
        default=None,
        help="Dotted module path overriding the KNOWN_CLIS lookup.",
    )
    pb.add_argument(
        "--out",
        default=None,
        help="Write to this path instead of stdout.",
    )
    pb.set_defaults(func=_cmd_bash)

    # ── zsh ───────────────────────────────────────────────────────────────
    pz = sub.add_parser("zsh", help="Render zsh completion for one CLI")
    pz.add_argument(
        "--program",
        default="ops_cli",
        help=f"Program name (default: ops_cli). Known: {', '.join(sorted(KNOWN_CLIS))}",
    )
    pz.add_argument(
        "--module",
        default=None,
        help="Dotted module path overriding the KNOWN_CLIS lookup.",
    )
    pz.add_argument(
        "--out",
        default=None,
        help="Write to this path instead of stdout.",
    )
    pz.set_defaults(func=_cmd_zsh)

    # ── all ───────────────────────────────────────────────────────────────
    pa = sub.add_parser(
        "all",
        help="Regenerate bash + zsh completion for every known CLI",
    )
    pa.add_argument(
        "--out-dir",
        default="docs/completion",
        help="Directory to write the generated scripts (default: docs/completion).",
    )
    pa.set_defaults(func=_cmd_all)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    """Parse ``argv`` and dispatch to the matching handler.

    Returns the desired process exit code:
      * ``0`` — handler ran cleanly
      * ``1`` — handler raised; the message was printed to stderr
      * ``2`` — argparse rejected the invocation
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
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
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
