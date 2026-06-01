"""``tools.completion_gen`` — auto-generate bash + zsh tab-completion scripts
from an ``argparse.ArgumentParser`` tree.

The ops CLI has grown past 30 top-level subcommands plus a deep nest of
sub-subcommands. Hand-maintaining a completion script that mirrors the
parser is a recipe for drift — every new ``sub.add_parser("...")`` would
need a matching shell change.

This module introspects the live parser instead. It walks the
``ArgumentParser`` + the ``_SubParsersAction`` groups under it and emits:

* ``render_bash_completion`` — a pure-bash ``complete -F`` hook function.
* ``render_zsh_completion`` — a ``#compdef`` script using ``_arguments``
  and ``_describe`` so descriptions show next to each subcommand.

The output is deterministic (subcommands are sorted alphabetically) so a
regenerated file produces a clean diff in CI.

What we DO NOT generate
-----------------------
* Completion for option VALUES (``--user-id <id>`` does not autocomplete
  the id). Operators just type the value; supporting dynamic data would
  require running Python at completion time.
* Anything that would execute the parser's handlers. Introspection walks
  the static structure only.

Pure stdlib — no external dependency. The generated scripts are
self-contained shell code; they do not call back into Python at
completion time.
"""
from __future__ import annotations

import argparse
from typing import Any, Optional


# ─────────────────────────────────────────────────────────────────────────────
#  Discovery
# ─────────────────────────────────────────────────────────────────────────────


def _collect_options(parser: argparse.ArgumentParser) -> list[str]:
    """Return the sorted, unique long/short option flags on ``parser``.

    We pull from ``parser._actions`` (the stdlib-blessed introspection
    handle — argparse exposes nothing public). Each action has an
    ``option_strings`` list; positional args have an empty list and are
    skipped. ``--help`` is included so completion of ``--<TAB>`` always
    shows it.
    """
    seen: set[str] = set()
    for action in parser._actions:  # noqa: SLF001 — stdlib introspection
        for opt in action.option_strings:
            if opt:
                seen.add(opt)
    return sorted(seen)


def _find_subparsers_action(
    parser: argparse.ArgumentParser,
) -> Optional[argparse._SubParsersAction]:
    """Locate the ``_SubParsersAction`` attached to ``parser`` if any.

    A parser can have at most one ``add_subparsers`` group in practice;
    the project's CLIs follow that convention. Returns ``None`` for a
    leaf parser with no nested subcommands.
    """
    for action in parser._actions:  # noqa: SLF001 — stdlib introspection
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def discover_subcommands(parser: argparse.ArgumentParser) -> dict[str, Any]:
    """Walk ``parser`` + every nested ``_SubParsersAction`` and return a
    nested dict the renderers can consume.

    Shape::

        {
          "subcommands": {
            "alerts": {
              "help": "Alert subcommands",
              "options": ["--help"],
              "subcommands": {
                "list": {"help": "...", "options": ["--help", "--json", ...],
                          "subcommands": {}},
                "ack":  {"help": "...", "options": [...], "subcommands": {}},
                ...
              },
            },
            ...
          },
          "top_options": ["--help", ...],
        }

    Subcommand names are sorted alphabetically so the rendered shell
    scripts diff cleanly between regenerations.
    """
    return {
        "subcommands": _walk_subparsers(parser),
        "top_options": _collect_options(parser),
    }


def _walk_subparsers(parser: argparse.ArgumentParser) -> dict[str, dict[str, Any]]:
    """Internal recursive helper. Returns the ``{name: {...}}`` map for
    ``parser``'s subparsers (or ``{}`` if it has no subparsers).
    """
    sub_action = _find_subparsers_action(parser)
    if sub_action is None:
        return {}

    # ``choices`` is an OrderedDict[name, ArgumentParser]; ``_choices_actions``
    # is the parallel list with help strings. We zip them via name so a
    # subparser that omitted ``help=`` doesn't blow up.
    help_by_name: dict[str, str] = {}
    for choice_action in getattr(sub_action, "_choices_actions", []):
        # ``dest`` here is the subcommand name (argparse stores it as
        # ``dest`` on _ChoicesPseudoAction).
        name = getattr(choice_action, "dest", None)
        if name:
            help_by_name[name] = (choice_action.help or "").strip()

    out: dict[str, dict[str, Any]] = {}
    for name in sorted(sub_action.choices.keys()):
        child = sub_action.choices[name]
        out[name] = {
            "help": help_by_name.get(name, ""),
            "options": _collect_options(child),
            "subcommands": _walk_subparsers(child),
        }
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Bash renderer
# ─────────────────────────────────────────────────────────────────────────────


# Bash function names must be valid identifiers — slot the sanitised
# program name into them. We do NOT want underscores in the user-facing
# program name (which can be e.g. ``ops_cli`` or ``python -m tools.ops``),
# only in the function names that bash actually evaluates.
def _bash_safe(name: str) -> str:
    """Sanitise ``name`` for use as a bash identifier."""
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in name) or "cli"


def render_bash_completion(
    parser: argparse.ArgumentParser,
    *,
    program_name: str = "cli",
) -> str:
    """Render a self-contained bash completion script for ``parser``.

    Wires ``complete -F _<program>_complete <program>`` at the bottom.
    The hook inspects ``${COMP_WORDS[@]}`` + ``$COMP_CWORD`` and walks
    down the discovered subcommand tree to suggest the right slice.

    Always renders something — even a parser with no subparsers produces
    a valid (if empty-bodied) completion that just suggests its options.
    """
    tree = discover_subcommands(parser)
    safe = _bash_safe(program_name)

    # Build a single ``case`` cascade keyed on the COMP_WORDS path. For
    # each level we emit "if previous-word == X then suggest Y" using a
    # nested case structure built bottom-up.
    top_subs = " ".join(sorted(tree["subcommands"].keys()))
    top_opts = " ".join(tree["top_options"])

    # The "first word after the program" case is special — it suggests
    # the top-level subcommand list.
    lines: list[str] = []
    lines.append(f"# Auto-generated bash completion for `{program_name}`.")
    lines.append("# Source this file or drop it into /etc/bash_completion.d/.")
    lines.append("# Regenerate via: python -m tools.completion_cli bash ...")
    lines.append("")
    lines.append(f"_{safe}_complete() {{")
    lines.append("    local cur prev words cword")
    lines.append("    COMPREPLY=()")
    lines.append("    cur=\"${COMP_WORDS[COMP_CWORD]}\"")
    lines.append("    # Build a space-joined trail of the subcommand path we've")
    lines.append("    # already typed (skipping options + the program name itself).")
    lines.append("    local trail=\"\"")
    lines.append("    local i")
    lines.append("    for (( i=1; i<COMP_CWORD; i++ )); do")
    lines.append("        local w=\"${COMP_WORDS[i]}\"")
    lines.append("        # Skip flags and their following value (best-effort —")
    lines.append("        # we don't know which flags take values, so we treat")
    lines.append("        # ``--foo=bar`` and ``--foo bar`` the same: just drop")
    lines.append("        # both tokens). This keeps trail-matching robust for the")
    lines.append("        # common operator pattern ``ops_cli alerts list --json``.")
    lines.append("        if [[ \"$w\" == -* ]]; then")
    lines.append("            continue")
    lines.append("        fi")
    lines.append("        if [[ -z \"$trail\" ]]; then")
    lines.append("            trail=\"$w\"")
    lines.append("        else")
    lines.append("            trail=\"$trail $w\"")
    lines.append("        fi")
    lines.append("    done")
    lines.append("")
    lines.append("    local suggestions=\"\"")
    lines.append("    case \"$trail\" in")
    # Empty trail → suggest top-level subcommands.
    if top_subs:
        lines.append("        \"\")")
        lines.append(f"            suggestions=\"{top_subs}\"")
        lines.append("            ;;")
    # Walk the tree and emit one case branch per known subcommand path.
    for branch in _bash_branches(tree["subcommands"], prefix=()):
        lines.append(branch)
    lines.append("        *)")
    lines.append("            suggestions=\"\"")
    lines.append("            ;;")
    lines.append("    esac")
    lines.append("")
    lines.append("    # If the current token starts with '-', also fall back to")
    lines.append("    # the parser's known long/short options. We don't try to")
    lines.append("    # scope these per-subcommand — operators rarely ask for")
    lines.append("    # option-name completion deep in the tree, and any miss is")
    lines.append("    # only a missing suggestion (never an incorrect one).")
    lines.append("    if [[ \"$cur\" == -* ]]; then")
    if top_opts:
        lines.append(f"        suggestions=\"$suggestions {top_opts}\"")
    lines.append("    fi")
    lines.append("")
    lines.append("    COMPREPLY=( $(compgen -W \"$suggestions\" -- \"$cur\") )")
    lines.append("    return 0")
    lines.append("}")
    lines.append("")
    # ``complete -F`` hook — bind to the bare program name. If the user
    # invokes via ``python -m tools.ops_cli`` the hook won't fire (bash
    # binds to argv[0]); the install instructions in DEPLOYMENT.md
    # explain the wrapper-script pattern for that case.
    lines.append(f"complete -F _{safe}_complete {program_name}")
    lines.append("")
    return "\n".join(lines)


def _bash_branches(
    subcommands: dict[str, dict[str, Any]],
    *,
    prefix: tuple[str, ...],
) -> list[str]:
    """Yield one ``case``-branch string per subcommand path in the tree.

    For each subcommand path we already have (e.g. ``alerts``), suggest
    its children (e.g. ``list ack ack-all metrics``). Leaf subcommands
    with no nested subparsers produce a branch with empty suggestions —
    handy because it short-circuits the wildcard fall-through.
    """
    out: list[str] = []
    for name in sorted(subcommands.keys()):
        info = subcommands[name]
        path = prefix + (name,)
        children = info.get("subcommands", {}) or {}
        trail = " ".join(path)
        child_names = " ".join(sorted(children.keys()))
        out.append(f"        \"{trail}\")")
        if child_names:
            out.append(f"            suggestions=\"{child_names}\"")
        else:
            out.append("            suggestions=\"\"")
        out.append("            ;;")
        if children:
            out.extend(_bash_branches(children, prefix=path))
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Zsh renderer
# ─────────────────────────────────────────────────────────────────────────────


def _zsh_escape(text: str) -> str:
    """Escape ``text`` for safe inclusion inside a zsh single-quoted
    string. Single quotes get the standard ``'\\''`` trick; backslashes
    are doubled. Newlines collapse to a space so a multi-line help blurb
    stays on one ``_describe`` row.
    """
    if not text:
        return ""
    text = text.replace("\\", "\\\\").replace("'", "'\\''")
    text = text.replace("\n", " ").replace("\r", " ")
    return text


def render_zsh_completion(
    parser: argparse.ArgumentParser,
    *,
    program_name: str = "cli",
) -> str:
    """Render a ``#compdef`` zsh completion script for ``parser``.

    Uses ``_arguments`` at the top level to choose between top subcommands
    and a per-subcommand ``_describe`` block for nested subcommands. The
    output is one self-contained file — drop it into a directory on
    ``$fpath`` named ``_<program>`` (e.g. ``_ops_cli``) and ``autoload``
    it. The completion shows the ``help=`` text from argparse next to
    each subcommand name.

    Always renders something even for a parser with no subparsers.
    """
    tree = discover_subcommands(parser)
    safe = _bash_safe(program_name)

    lines: list[str] = []
    lines.append(f"#compdef {program_name}")
    lines.append("# Auto-generated zsh completion. Regenerate via:")
    lines.append(f"#   python -m tools.completion_cli zsh --program {program_name} --out _<program>")
    lines.append("# Install: drop this file into a directory on $fpath as ``_<program>``,")
    lines.append("# then ``autoload -U _<program> && compinit``.")
    lines.append("")
    lines.append(f"_{safe}() {{")
    lines.append("    local -a _commands")
    lines.append("    local context state state_descr line")
    lines.append("    typeset -A opt_args")
    lines.append("")
    lines.append("    _arguments -C \\")
    lines.append("        '1: :->command' \\")
    lines.append("        '*::arg:->args'")
    lines.append("")
    lines.append("    case $state in")
    lines.append("        command)")
    lines.append("            _commands=(")
    for name in sorted(tree["subcommands"].keys()):
        info = tree["subcommands"][name]
        help_text = _zsh_escape(info.get("help", ""))
        lines.append(f"                '{name}:{help_text}'")
    lines.append("            )")
    lines.append(f"            _describe -t commands '{program_name} command' _commands")
    lines.append("            ;;")
    lines.append("        args)")
    lines.append("            case $line[1] in")
    # One per-subcommand branch with its nested subcommands. Each branch
    # follows the same template: open the case arm, emit nested-subcommand
    # suggestions if any, then close with ``;;``. Leaves emit a comment
    # placeholder so the generated file stays diff-stable across runs.
    for name in sorted(tree["subcommands"].keys()):
        info = tree["subcommands"][name]
        children = info.get("subcommands", {}) or {}
        lines.append(f"                {name})")
        if children:
            lines.append("                    local -a _subs")
            lines.append("                    _subs=(")
            for sub_name in sorted(children.keys()):
                sub_info = children[sub_name]
                sub_help = _zsh_escape(sub_info.get("help", ""))
                lines.append(f"                        '{sub_name}:{sub_help}'")
            lines.append("                    )")
            lines.append(f"                    _describe -t commands '{name} subcommand' _subs")
        else:
            lines.append("                    # (no nested subcommands)")
        lines.append("                    ;;")
    lines.append("                *)")
    lines.append("                    ;;")
    lines.append("            esac")
    lines.append("            ;;")
    lines.append("    esac")
    lines.append("}")
    lines.append("")
    # ``#compdef`` at the top is enough — zsh auto-runs the named function.
    # The explicit invocation below makes the file also work when sourced
    # directly (e.g. in CI where compinit hasn't been initialised yet).
    lines.append(f"_{safe} \"$@\"")
    lines.append("")
    return "\n".join(lines)
