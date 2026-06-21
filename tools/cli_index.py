"""``tools.cli_index`` — auto-generate a CLI registry + markdown index.

The repo has ~15 operator-facing CLIs under ``tools/`` and (eventually)
``cli/``. Onboarding new operators has been painful — "what tools exist?"
required ``grep -l 'if __name__ == "__main__"' tools/*.py`` followed by
``head -20`` on every hit. Worse, the lack of a single index has led to
near-duplicate tools being scoped because nobody remembered the existing
one.

This module walks ``tools/*.py`` + ``cli/*.py``, introspects every true
CLI (module with an ``if __name__ == "__main__"`` block), and emits:

* ``docs/CLI_INDEX.md``    — human-readable, one section per CLI grouped
                              by source directory.
* ``docs/cli_registry.json`` — machine-readable equivalent for downstream
                              tooling (Slack-bot doc lookups, etc.).

Discovery strategy — two-tier:

1. **Live introspection.** ``importlib.import_module(<dotted>)`` then
   inspect module attrs for ``argparse.ArgumentParser`` instances. If
   the module exposes a ``_build_parser()`` factory (the project
   convention for subcommand-heavy CLIs like ``ops_cli``), we call it
   and read prog / description / actions off the returned parser.
2. **AST fallback.** Some CLIs build the parser inside ``main()`` (e.g.
   ``styles_audit``, ``port_supply_diff``, ``backtests``). When live
   introspection comes up empty we parse the source with ``ast`` and
   walk for ``ArgumentParser(...)`` constructor calls + every
   ``add_argument(...)`` / ``add_parser(...)`` call. The fallback only
   captures flag names + the help= string + first positional kwarg, but
   that's enough for an index entry.

Anything we can't parse falls through to a placeholder entry rather
than being silently dropped — operators need to know a CLI exists
even when the introspector chokes on it.

Usage::

    # Regenerate the docs in-place (default paths under docs/).
    python -m tools.cli_index

    # Custom output paths.
    python -m tools.cli_index --out-md /tmp/INDEX.md \\
        --out-json /tmp/registry.json

    # Quiet mode for cron / CI.
    python -m tools.cli_index --quiet

    # Verify-only mode — no writes; exits 1 if the on-disk docs are
    # stale (the current discovery differs from the committed files).
    # This is the CI gate.
    python -m tools.cli_index --verify

Exit codes:
  0   success (or, with --verify, on-disk docs are in sync)
  1   --verify detected drift (or a write failed)
  2   bad argument
"""
from __future__ import annotations

import argparse
import ast
import importlib
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable


__all__ = [
    "ArgSpec",
    "CliSpec",
    "discover_clis",
    "build_registry",
    "render_markdown",
    "render_json",
    "main",
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ArgSpec:
    """One CLI argument's introspected shape."""

    name: str                       # "--out" or "alert_id" (positional)
    help: str = ""
    default: Any = None             # JSON-serialisable; falls back to repr()
    required: bool = False
    choices: list[str] = field(default_factory=list)
    is_positional: bool = False
    is_flag: bool = False           # store_true / store_false / count


@dataclass
class CliSpec:
    """One CLI's introspected entry."""

    directory: str                  # "tools" or "cli"
    module: str                     # "tools.port_supply_diff"
    file_path: str                  # repo-relative POSIX path (machine-independent)
    prog: str                       # "python -m tools.port_supply_diff"
    description: str = ""
    docstring: str = ""             # first paragraph of the module docstring
    args: list[ArgSpec] = field(default_factory=list)
    discovered_via: str = ""        # "live", "factory", "ast", "placeholder"
    parse_error: str = ""           # populated when discovery fell back


# ---------------------------------------------------------------------------
# Source-file filter — only true CLIs
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _strip_repo_root(text: str) -> str:
    """Make rendered docs machine-INDEPENDENT: strip the absolute repo-root
    prefix from any path in ``text`` so the committed output is identical across
    dev machines and CI — no ``/Users/<name>/...`` vs ``/home/runner/...`` drift
    (which made the docs perpetually "stale" in CI), and no local username /
    absolute path leaked into committed files. Handles POSIX + Windows separators.
    """
    root = str(_REPO_ROOT)
    return (text.replace(root + "/", "")
                .replace(root + "\\", "")
                .replace(root, ""))

# Files that look like helper modules even though they live in tools/.
# We keep this list narrow — the source filter below already excludes
# anything without an ``if __name__ == "__main__"`` block.
_HELPER_PREFIXES = ("_",)


def _is_cli_file(path: Path) -> bool:
    """Decide whether ``path`` is a true CLI (worth indexing).

    Rules:
      * filename must be ``*.py``
      * filename must not be ``__init__.py``
      * filename must not start with ``_`` (helper module convention)
      * source must contain ``if __name__ == "__main__"``

    Defensive: read errors → file is skipped.
    """
    if path.suffix != ".py":
        return False
    if path.name == "__init__.py":
        return False
    if any(path.name.startswith(p) for p in _HELPER_PREFIXES):
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return 'if __name__ == "__main__"' in text or "if __name__ == '__main__'" in text


def _discover_files() -> list[Path]:
    """Return a sorted list of CLI files across tools/ + cli/.

    Order: directory ('cli' < 'tools' alphabetically), then filename.
    The deterministic order keeps the rendered docs diff-clean across
    runs.
    """
    candidates: list[Path] = []
    for dirname in ("cli", "tools"):
        dir_path = _REPO_ROOT / dirname
        if not dir_path.is_dir():
            continue
        for path in sorted(dir_path.glob("*.py")):
            if _is_cli_file(path):
                candidates.append(path)
    return candidates


# ---------------------------------------------------------------------------
# Live / factory introspection — preferred path
# ---------------------------------------------------------------------------


def _dotted_module(path: Path) -> str:
    """Convert ``/.../tools/foo.py`` → ``tools.foo``."""
    rel = path.relative_to(_REPO_ROOT)
    parts = list(rel.with_suffix("").parts)
    return ".".join(parts)


def _safe_serialise(value: Any) -> Any:
    """Coerce ``value`` to something json.dumps can swallow.

    argparse defaults are often ``Path`` / sentinel / a callable; we
    don't want any of them to break the JSON dump.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_serialise(v) for v in value]
    return repr(value)


def _action_to_argspec(action: argparse.Action) -> ArgSpec | None:
    """Convert one ``argparse.Action`` into an ``ArgSpec``.

    Returns ``None`` for the auto-injected ``--help`` action — there's
    no operator value in surfacing it in every CLI's arg table.
    """
    if isinstance(action, argparse._HelpAction):
        return None
    if isinstance(action, argparse._SubParsersAction):
        # Subparsers are handled separately — skip the container.
        return None

    if action.option_strings:
        # Optional / flag — use the first long form if present.
        name = sorted(
            action.option_strings,
            key=lambda s: (not s.startswith("--"), len(s)),
        )[0]
        positional = False
    else:
        name = action.dest
        positional = True

    is_flag = isinstance(
        action,
        (
            argparse._StoreTrueAction,
            argparse._StoreFalseAction,
            argparse._CountAction,
        ),
    )

    choices = list(action.choices) if action.choices else []
    return ArgSpec(
        name=name,
        help=(action.help or "").strip(),
        default=_safe_serialise(action.default),
        required=bool(getattr(action, "required", False)),
        choices=[str(c) for c in choices],
        is_positional=positional,
        is_flag=is_flag,
    )


def _walk_parser(parser: argparse.ArgumentParser) -> list[ArgSpec]:
    """Flatten ``parser`` + every nested ``_SubParsersAction`` into args.

    Subparsers are rendered as one ``ArgSpec`` per subcommand with the
    name ``<sub>`` and help describing the subcommand. We don't recurse
    into the subparser's own options here — the markdown stays scannable
    and operators can run ``--help`` for full detail.
    """
    out: list[ArgSpec] = []
    for action in parser._actions:  # noqa: SLF001 — stdlib introspection
        if isinstance(action, argparse._SubParsersAction):
            for sub_name, sub_parser in sorted(action.choices.items()):
                # The subcommand description / help — pull from the
                # _SubParsersAction's choices_actions list if present.
                sub_help = (sub_parser.description or "").strip()
                if not sub_help:
                    # Fall back to the parser's prog tail.
                    sub_help = f"subcommand: {sub_name}"
                out.append(ArgSpec(
                    name=f"<{sub_name}>",
                    help=sub_help,
                    is_positional=True,
                ))
            continue
        spec = _action_to_argspec(action)
        if spec is not None:
            out.append(spec)
    return out


def _try_live(module_name: str) -> tuple[
    argparse.ArgumentParser | None, str, str
]:
    """Try to obtain a live parser for ``module_name``.

    Returns ``(parser, mode, error)``:
      * parser is the live ArgumentParser instance or None
      * mode is "factory" (called ``_build_parser``), "live" (found an
        ArgumentParser attr), or "" (nothing worked)
      * error is the exception string when import / parser construction
        raised; empty otherwise.

    Importing the module is itself fallible (missing optional deps,
    heavy import-time side effects, etc.); any failure here returns
    ``(None, "", error)`` and the caller falls through to AST parsing.
    """
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 — discovery must never raise
        return None, "", f"{type(exc).__name__}: {exc}"

    # Preferred: factory function (the project's convention for
    # subcommand-heavy CLIs).
    builder = getattr(module, "_build_parser", None)
    if callable(builder):
        try:
            parser = builder()
        except Exception as exc:  # noqa: BLE001
            return None, "", f"_build_parser raised {type(exc).__name__}: {exc}"
        if isinstance(parser, argparse.ArgumentParser):
            return parser, "factory", ""

    # Fallback: module-level ArgumentParser instance.
    for attr_name in dir(module):
        if attr_name.startswith("__"):
            continue
        try:
            attr = getattr(module, attr_name)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(attr, argparse.ArgumentParser):
            return attr, "live", ""

    return None, "", ""


# ---------------------------------------------------------------------------
# AST fallback — for CLIs whose parser is built inside main()
# ---------------------------------------------------------------------------


def _flatten_string_node(node: ast.AST) -> str:
    """Render an AST string-ish node back to a flat string.

    Handles:
      * ``ast.Constant`` with a str value
      * ``ast.JoinedStr`` (f-strings) — literal parts kept verbatim,
        ``ast.FormattedValue`` parts rendered as ``{<expr-source>}``
      * Adjacent ``ast.Constant`` strings joined by ``ast.BinOp(+)``
        (the implicit-concat pattern via ``+``)
      * Parenthesised string literals split across lines (``ast.Constant``
        only — Python's parser already concatenates adjacent literals)

    Returns "" for anything else.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                # Best-effort: surface the formatted expression as a
                # placeholder. We don't try to evaluate it.
                try:
                    expr_src = ast.unparse(v.value)
                except Exception:  # noqa: BLE001
                    expr_src = "?"
                parts.append("{" + expr_src + "}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return (
            _flatten_string_node(node.left)
            + _flatten_string_node(node.right)
        )
    return ""


def _first_string_kwarg(call: ast.Call, *names: str) -> str:
    """Return the first matching string kwarg from ``call``.

    Walks ``call.keywords`` looking for the first kwarg whose name is
    in ``names`` and whose value flattens to a non-empty string. We
    accept literal strings, f-strings, and string-concat expressions
    so help text spread across continuation lines still gets captured.
    Returns empty string when no match.
    """
    for kw in call.keywords:
        if kw.arg in names:
            s = _flatten_string_node(kw.value).strip()
            if s:
                return s
    return ""


def _first_string_arg(call: ast.Call) -> str:
    """Return the first positional string-literal argument of ``call``."""
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return ""


def _is_argparse_add(call: ast.Call, method_names: set[str]) -> bool:
    """True when ``call`` looks like ``X.add_argument(...)`` /
    ``X.add_parser(...)`` (or whichever method name(s) we're hunting).

    We rely on attribute-name matching because we don't track variable
    types through the AST — this overcollects slightly (any object with
    an ``add_argument`` method matches) but argparse is the only one
    the project uses and the cost of a false positive is one bogus row
    in an arg table, not a crash.
    """
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr in method_names
    )


def _is_argparse_constructor(call: ast.Call) -> bool:
    """True when ``call`` looks like ``argparse.ArgumentParser(...)`` or
    ``ArgumentParser(...)``."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "ArgumentParser":
        return True
    if isinstance(func, ast.Name) and func.id == "ArgumentParser":
        return True
    return False


def _collect_module_constants(tree: ast.Module) -> dict[str, Any]:
    """Best-effort map of module-level constant assignments.

    Walks top-level ``Name = <Constant>`` statements and captures the
    value. Used to resolve ``default=_DEFAULT_FOO`` patterns where the
    AST parser would otherwise miss the literal default. Nothing fancy:
    only direct constant RHS, no expression evaluation.
    """
    out: dict[str, Any] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and isinstance(
                    stmt.value, ast.Constant
                ):
                    out[target.id] = stmt.value.value
    return out


def _astparse_file(path: Path) -> tuple[
    str, str, list[ArgSpec], str
]:
    """AST-walk ``path`` for parser info.

    Returns ``(prog, description, args, parse_error)``. Empty strings
    for prog/description if no constructor was found. The arg list is
    everything ``add_argument`` returned — we don't try to nest by
    subparser; subparsers show up as the bare add_parser name.
    """
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return "", "", [], f"ast parse failed: {type(exc).__name__}: {exc}"

    module_consts = _collect_module_constants(tree)
    prog = ""
    description = ""
    args: list[ArgSpec] = []
    subcommand_names: list[tuple[str, str]] = []  # (name, help)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Constructor — pick up prog + description.
        if _is_argparse_constructor(node):
            new_prog = _first_string_kwarg(node, "prog")
            new_desc = _first_string_kwarg(node, "description")
            if new_prog and not prog:
                prog = new_prog
            if new_desc and not description:
                description = new_desc
            continue
        # add_argument(...) → ArgSpec
        if _is_argparse_add(node, {"add_argument"}):
            name = _first_string_arg(node)
            if not name:
                continue
            help_str = _first_string_kwarg(node, "help")
            choices: list[str] = []
            for kw in node.keywords:
                if kw.arg == "choices" and isinstance(
                    kw.value, (ast.List, ast.Tuple)
                ):
                    for elt in kw.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(
                            elt.value, str
                        ):
                            choices.append(elt.value)
            required = False
            for kw in node.keywords:
                if kw.arg == "required" and isinstance(
                    kw.value, ast.Constant
                ):
                    required = bool(kw.value.value)
            is_flag = False
            default_val: Any = None
            for kw in node.keywords:
                if kw.arg == "action" and isinstance(
                    kw.value, ast.Constant
                ):
                    if kw.value.value in (
                        "store_true",
                        "store_false",
                        "count",
                    ):
                        is_flag = True
                if kw.arg == "default":
                    if isinstance(kw.value, ast.Constant):
                        default_val = kw.value.value
                    elif isinstance(kw.value, ast.Name) and (
                        kw.value.id in module_consts
                    ):
                        default_val = module_consts[kw.value.id]
            positional = not name.startswith("-")
            args.append(ArgSpec(
                name=name,
                help=help_str,
                default=_safe_serialise(default_val),
                required=required,
                choices=choices,
                is_positional=positional,
                is_flag=is_flag,
            ))
            continue
        # add_parser(...) → a synthetic positional <name> entry
        if _is_argparse_add(node, {"add_parser"}):
            name = _first_string_arg(node)
            if not name:
                continue
            help_str = _first_string_kwarg(node, "help", "description")
            subcommand_names.append((name, help_str))

    # Sort + dedupe subcommand names; keep first-seen help.
    seen: dict[str, str] = {}
    for name, help_str in subcommand_names:
        if name not in seen and help_str:
            seen[name] = help_str
        elif name not in seen:
            seen[name] = ""
    for sub_name in sorted(seen):
        args.append(ArgSpec(
            name=f"<{sub_name}>",
            help=seen[sub_name] or f"subcommand: {sub_name}",
            is_positional=True,
        ))

    return prog, description, args, ""


# ---------------------------------------------------------------------------
# Docstring extraction — first paragraph only
# ---------------------------------------------------------------------------


def _extract_docstring(path: Path) -> str:
    """Return the module-level docstring (first paragraph) or empty.

    AST-based to avoid importing the module a second time. Defensive
    against unparseable source.
    """
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ""
    raw = ast.get_docstring(tree) or ""
    if not raw:
        return ""
    paragraphs = raw.split("\n\n")
    return paragraphs[0].strip()


# ---------------------------------------------------------------------------
# Discovery — combine live + AST paths into one CliSpec per file
# ---------------------------------------------------------------------------


def _spec_from_file(path: Path, *, verbose: bool = False) -> CliSpec:
    """Build a ``CliSpec`` for one CLI file.

    Discovery is best-effort: every failure path logs to stderr (when
    ``verbose=True``) but never raises. The worst-case output is a
    placeholder entry with the module path + docstring and no args.
    """
    rel = path.relative_to(_REPO_ROOT)
    directory = rel.parts[0]
    dotted = _dotted_module(path)
    docstring = _extract_docstring(path)

    # Default prog: how operators actually invoke it.
    default_prog = f"python -m {dotted}"

    parser, mode, live_err = _try_live(dotted)
    if parser is not None:
        prog = parser.prog or default_prog
        description = (parser.description or docstring).strip()
        args = _walk_parser(parser)
        return CliSpec(
            directory=directory,
            module=dotted,
            file_path=rel.as_posix(),
            prog=prog,
            description=description,
            docstring=docstring,
            args=args,
            discovered_via=mode,
            parse_error="",
        )

    # Live failed (or returned no parser) — try AST.
    ast_prog, ast_desc, ast_args, ast_err = _astparse_file(path)
    if ast_args or ast_prog or ast_desc:
        prog = ast_prog or default_prog
        if not prog.startswith("python") and prog:
            # Normalise "tools.foo" → "python -m tools.foo".
            prog = f"python -m {prog}"
        description = (ast_desc or docstring).strip()
        return CliSpec(
            directory=directory,
            module=dotted,
            file_path=rel.as_posix(),
            prog=prog,
            description=description,
            docstring=docstring,
            args=ast_args,
            discovered_via="ast",
            parse_error=live_err or ast_err,
        )

    # Last resort — placeholder. The operator at least sees the CLI exists.
    if verbose:
        print(
            f"cli_index: placeholder entry for {dotted} "
            f"(live: {live_err or 'no parser'}, ast: {ast_err or 'empty'})",
            file=sys.stderr,
        )
    return CliSpec(
        directory=directory,
        module=dotted,
        file_path=str(path.resolve()),
        prog=default_prog,
        description=docstring,
        docstring=docstring,
        args=[],
        discovered_via="placeholder",
        parse_error=live_err or ast_err,
    )


def discover_clis(*, verbose: bool = False) -> list[CliSpec]:
    """Walk tools/ + cli/ and return a CliSpec per true CLI module.

    Sorted by (directory, module). Deterministic for diff-friendly
    regeneration.
    """
    out: list[CliSpec] = []
    for path in _discover_files():
        out.append(_spec_from_file(path, verbose=verbose))
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _argspec_to_dict(arg: ArgSpec) -> dict:
    return {
        "name":          arg.name,
        "help":          arg.help,
        "default":       arg.default,
        "required":      arg.required,
        "choices":       list(arg.choices),
        "is_positional": arg.is_positional,
        "is_flag":       arg.is_flag,
    }


def _cli_to_dict(cli: CliSpec) -> dict:
    return {
        "directory":      cli.directory,
        "module":         cli.module,
        "file_path":      cli.file_path,
        "prog":           cli.prog,
        "description":    cli.description,
        "docstring":      cli.docstring,
        "args":           [_argspec_to_dict(a) for a in cli.args],
        "discovered_via": cli.discovered_via,
        "parse_error":    cli.parse_error,
    }


def build_registry(clis: Iterable[CliSpec]) -> dict:
    """Build the registry payload (the JSON output body)."""
    clis_list = list(clis)
    return {
        "version": 1,
        "total":   len(clis_list),
        "clis":    [_cli_to_dict(c) for c in clis_list],
    }


def render_json(clis: Iterable[CliSpec]) -> str:
    """Render the JSON registry — pretty-printed, sorted keys for diff."""
    payload = build_registry(clis)
    return _strip_repo_root(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _format_default(arg: ArgSpec) -> str:
    """Render the default-column cell for the markdown arg table."""
    if arg.required:
        return "_required_"
    if arg.is_flag:
        return "false"
    if arg.default is None:
        return "—"
    if isinstance(arg.default, str):
        if not arg.default:
            return '""'
        return f"`{arg.default}`"
    return f"`{arg.default}`"


def _format_choices(arg: ArgSpec) -> str:
    if not arg.choices:
        return ""
    return ", ".join(f"`{c}`" for c in arg.choices)


def _render_cli_section(cli: CliSpec) -> list[str]:
    """Render one CLI's markdown section.

    Layout:
      ### tools.foo
      one-line description
      ```
      python -m tools.foo --bar VALUE
      ```
      | Arg | Default | Choices | Help |
      | ... |
    """
    lines: list[str] = []
    lines.append(f"### `{cli.module}`")
    lines.append("")
    if cli.description:
        lines.append(cli.description)
        lines.append("")
    lines.append("```")
    lines.append(cli.prog)
    lines.append("```")
    lines.append("")
    if cli.discovered_via in ("placeholder",):
        lines.append(
            "_NOTE: introspection failed — see the module's docstring "
            "for usage. Discovery error logged in the JSON registry._"
        )
        lines.append("")
    if not cli.args:
        if cli.discovered_via != "placeholder":
            lines.append("_No arguments._")
            lines.append("")
        return lines

    lines.append("| Arg | Required | Default | Choices | Help |")
    lines.append("| --- | --- | --- | --- | --- |")
    sorted_args = sorted(
        cli.args,
        key=lambda a: (not a.is_positional, a.name),
    )
    for a in sorted_args:
        req = "yes" if a.required else ""
        default = _format_default(a)
        choices = _format_choices(a)
        help_str = a.help.replace("\n", " ").replace("|", "\\|")
        if not help_str:
            help_str = "_(no help)_"
        lines.append(
            f"| `{a.name}` | {req} | {default} | {choices} | {help_str} |"
        )
    lines.append("")
    return lines


def render_markdown(clis: Iterable[CliSpec]) -> str:
    """Render the human-facing CLI index as markdown.

    Sections are grouped by directory (``## cli/`` then ``## tools/``).
    Within a section CLIs are listed alphabetically by module name.
    """
    clis_list = sorted(
        clis,
        key=lambda c: (c.directory, c.module),
    )
    by_dir: dict[str, list[CliSpec]] = {}
    for c in clis_list:
        by_dir.setdefault(c.directory, []).append(c)

    lines: list[str] = [
        "# CLI Index",
        "",
        (
            "Auto-generated by `python -m tools.cli_index`. Do not "
            "edit by hand — regenerate after adding a CLI."
        ),
        "",
        f"**Total CLIs:** {len(clis_list)}",
        "",
    ]

    # Table of contents — one row per CLI.
    lines.append("## Contents")
    lines.append("")
    lines.append("| Module | Directory | Description |")
    lines.append("| --- | --- | --- |")
    for c in clis_list:
        desc = (c.description or c.docstring or "").splitlines()[0:1]
        desc_text = desc[0] if desc else ""
        desc_text = desc_text.replace("|", "\\|")
        lines.append(
            f"| `{c.module}` | `{c.directory}/` | {desc_text} |"
        )
    lines.append("")

    for directory in sorted(by_dir):
        lines.append(f"## `{directory}/`")
        lines.append("")
        for cli in by_dir[directory]:
            lines.extend(_render_cli_section(cli))

    # Single trailing newline — POSIX text-file convention.
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    return _strip_repo_root(text)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


_DEFAULT_MD = "docs/CLI_INDEX.md"
_DEFAULT_JSON = "docs/cli_registry.json"


def _write_if_changed(path: Path, content: str) -> bool:
    """Write ``content`` to ``path`` unless it already matches.

    Returns True when the file was written, False when it was already
    in sync. Creates parent dirs as needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            return False
    path.write_text(content, encoding="utf-8")
    return True


def _verify(
    md_path: Path,
    json_path: Path,
    md_content: str,
    json_content: str,
) -> tuple[bool, list[str]]:
    """Compare ``md_content`` / ``json_content`` against on-disk files.

    Returns ``(ok, drifts)``. ``ok`` is True iff both files match
    byte-for-byte. ``drifts`` is a list of human-readable messages
    suitable for printing to stderr.
    """
    drifts: list[str] = []
    if not md_path.exists():
        drifts.append(f"missing: {md_path}")
    else:
        existing_md = md_path.read_text(encoding="utf-8")
        if existing_md != md_content:
            drifts.append(f"drift: {md_path} differs from regenerated content")
    if not json_path.exists():
        drifts.append(f"missing: {json_path}")
    else:
        existing_json = json_path.read_text(encoding="utf-8")
        if existing_json != json_content:
            drifts.append(
                f"drift: {json_path} differs from regenerated content"
            )
    return (not drifts), drifts


def _build_parser() -> argparse.ArgumentParser:
    """Construct the cli_index CLI parser.

    Exposed as a factory so the project's standard introspection path
    (``importlib.import_module(...).build_parser``) — including
    ``tools.completion_cli`` and ``tools.cli_index`` itself — picks the
    parser up without invoking ``main()``.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tools.cli_index",
        description=(
            "Walk tools/ + cli/ and emit a CLI index markdown + JSON "
            "registry. Use --verify in CI to gate doc drift."
        ),
    )
    parser.add_argument(
        "--out-md",
        default=_DEFAULT_MD,
        help=(
            "Markdown output path "
            "(default: docs/CLI_INDEX.md)."
        ),
    )
    parser.add_argument(
        "--out-json",
        default=_DEFAULT_JSON,
        help=(
            "JSON registry output path "
            "(default: docs/cli_registry.json)."
        ),
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress stdout summary (useful for cron / CI).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Verify-only mode — no writes. Exits 1 if the on-disk "
            "files differ from what the discoverer would produce. "
            "The CI gate."
        ),
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help=(
            "Log discovery failures to stderr (each CLI that falls "
            "through to a placeholder entry)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    clis = discover_clis(verbose=args.verbose)
    md_content = render_markdown(clis)
    json_content = render_json(clis)

    md_path = (_REPO_ROOT / args.out_md) if not Path(args.out_md).is_absolute() \
        else Path(args.out_md)
    json_path = (_REPO_ROOT / args.out_json) if not Path(args.out_json).is_absolute() \
        else Path(args.out_json)

    if args.verify:
        ok, drifts = _verify(md_path, json_path, md_content, json_content)
        if ok:
            if not args.quiet:
                print(
                    f"cli_index: OK — {len(clis)} CLI(s) discovered, "
                    f"on-disk docs in sync."
                )
            return 0
        for msg in drifts:
            print(f"cli_index: {msg}", file=sys.stderr)
        print(
            "cli_index: regenerate with `python -m tools.cli_index` "
            "and commit the result.",
            file=sys.stderr,
        )
        return 1

    md_written = _write_if_changed(md_path, md_content)
    json_written = _write_if_changed(json_path, json_content)

    if not args.quiet:
        placeholders = sum(1 for c in clis if c.discovered_via == "placeholder")
        ast_used = sum(1 for c in clis if c.discovered_via == "ast")
        factory_used = sum(1 for c in clis if c.discovered_via == "factory")
        live_used = sum(1 for c in clis if c.discovered_via == "live")
        print(
            f"cli_index: {len(clis)} CLI(s) — "
            f"{factory_used} via _build_parser, "
            f"{live_used} via module attr, "
            f"{ast_used} via AST, "
            f"{placeholders} placeholder"
        )
        print(
            f"  markdown: {md_path} "
            f"({'wrote' if md_written else 'in sync'})"
        )
        print(
            f"  json:     {json_path} "
            f"({'wrote' if json_written else 'in sync'})"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
