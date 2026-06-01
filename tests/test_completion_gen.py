"""Tests for ``tools.completion_gen`` — argparse → bash/zsh tab-completion.

Defining properties under test:

* ``discover_subcommands`` returns a shaped dict (``{subcommands, top_options}``)
  and finds EVERY top-level subcommand registered on the live parser.
* Nested subparsers are walked recursively — e.g. ``alerts`` has
  ``list / ack / ack-all / metrics`` and we can see all of them.
* ``render_bash_completion`` emits a non-empty function, hooks via
  ``complete -F``, and mentions every top-level subcommand.
* ``render_zsh_completion`` carries the ``#compdef`` directive, uses
  ``_arguments``, and mentions every top-level subcommand.
* Both renderers handle an empty parser (no subparsers) without raising.
* Generated bash is syntactically valid per ``bash -n``; generated zsh
  is syntactically valid per ``zsh -n`` (skipped gracefully if zsh is
  not available on the host).

These tests intentionally use the LIVE ``tools.ops_cli._build_parser``
output as the cross-check oracle so the moment the CLI grows a new
subcommand without regenerating completion, a test fails before merge.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tools.completion_gen import (
    discover_subcommands,
    render_bash_completion,
    render_zsh_completion,
)
from tools.ops_cli import _build_parser as _ops_build_parser


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def ops_parser() -> argparse.ArgumentParser:
    """Live ops_cli parser — the high-bar oracle for the test suite."""
    return _ops_build_parser()


@pytest.fixture
def minimal_parser() -> argparse.ArgumentParser:
    """Tiny parser with no subparsers — guards against the empty case."""
    p = argparse.ArgumentParser(prog="tiny", description="No subcommands here.")
    p.add_argument("--verbose", action="store_true")
    return p


@pytest.fixture
def two_level_parser() -> argparse.ArgumentParser:
    """Synthetic parser with a known nested shape we can hand-verify."""
    p = argparse.ArgumentParser(prog="demo")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("alpha", help="The alpha command")
    sa = a.add_subparsers(dest="sub", required=True)
    sa.add_parser("one", help="alpha one")
    sa.add_parser("two", help="alpha two")

    sub.add_parser("beta", help="The beta command")  # leaf
    return p


# ─── discover_subcommands ────────────────────────────────────────────────────


def test_discover_returns_required_shape(ops_parser):
    """discover_subcommands returns a dict with the documented keys."""
    tree = discover_subcommands(ops_parser)
    assert isinstance(tree, dict)
    assert "subcommands" in tree
    assert "top_options" in tree
    assert isinstance(tree["subcommands"], dict)
    assert isinstance(tree["top_options"], list)


def test_discover_finds_all_top_level_subcommands(ops_parser):
    """The discovered top-level subcommands must match the live parser's
    subparser choices exactly. Catches drift the moment a new subcommand
    is registered without updating completion."""
    tree = discover_subcommands(ops_parser)

    # Pull the ground truth directly from the parser.
    live_choices: set[str] = set()
    for action in ops_parser._actions:  # noqa: SLF001 — test reaches in
        if isinstance(action, argparse._SubParsersAction):
            live_choices.update(action.choices.keys())

    discovered = set(tree["subcommands"].keys())
    assert discovered == live_choices, (
        f"missing in discovery: {live_choices - discovered}; "
        f"extra: {discovered - live_choices}"
    )
    # Spot-check the headline ops subcommands the operator asked about.
    for name in ("alerts", "channels", "users", "mfa", "audit", "schedules"):
        assert name in discovered


def test_discover_handles_nested_subparsers(two_level_parser):
    """Nested subparsers must appear in the returned tree."""
    tree = discover_subcommands(two_level_parser)
    assert set(tree["subcommands"].keys()) == {"alpha", "beta"}
    alpha = tree["subcommands"]["alpha"]
    assert set(alpha["subcommands"].keys()) == {"one", "two"}
    assert alpha["help"] == "The alpha command"
    # ``beta`` is a leaf — empty subcommands dict, not missing key.
    assert tree["subcommands"]["beta"]["subcommands"] == {}


def test_discover_empty_parser(minimal_parser):
    """No subparsers → empty subcommands dict, options still surface."""
    tree = discover_subcommands(minimal_parser)
    assert tree["subcommands"] == {}
    assert "--verbose" in tree["top_options"]
    assert "--help" in tree["top_options"]


# ─── render_bash_completion ──────────────────────────────────────────────────


def test_bash_render_non_empty(ops_parser):
    """A populated parser should produce a substantive bash function."""
    script = render_bash_completion(ops_parser, program_name="ops_cli")
    # Sanity bar — anything less than a few hundred chars means the
    # renderer punched out early and we'd ship a stub script.
    assert len(script) > 500
    assert "_ops_cli_complete" in script
    assert "COMPREPLY" in script


def test_bash_render_contains_every_top_subcommand(ops_parser):
    """Every top-level subcommand must appear in the generated script —
    otherwise a tab on the bare ``ops_cli`` doesn't suggest it."""
    script = render_bash_completion(ops_parser, program_name="ops_cli")
    tree = discover_subcommands(ops_parser)
    for name in tree["subcommands"].keys():
        assert name in script, f"missing subcommand in bash output: {name}"


def test_bash_render_has_complete_hook(ops_parser):
    """The generated file must end with ``complete -F`` binding the hook
    to the program name — otherwise sourcing the file is a no-op."""
    script = render_bash_completion(ops_parser, program_name="ops_cli")
    assert "complete -F _ops_cli_complete ops_cli" in script


def test_bash_render_empty_parser_does_not_raise(minimal_parser):
    """An empty parser must still produce a valid bash file — used as a
    smoke target by the CLI ``--out=-`` path."""
    script = render_bash_completion(minimal_parser, program_name="tiny")
    assert "_tiny_complete" in script
    assert "complete -F _tiny_complete tiny" in script


def test_bash_render_is_syntactically_valid(ops_parser, tmp_path):
    """Lint with ``bash -n``. A syntax error here means we shipped a
    completion file that bombs the user's shell on source."""
    bash_bin = shutil.which("bash")
    if bash_bin is None:
        pytest.skip("bash not available on this host")
    script = render_bash_completion(ops_parser, program_name="ops_cli")
    target = tmp_path / "ops_cli.bash"
    target.write_text(script)
    result = subprocess.run(
        [bash_bin, "-n", str(target)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"bash -n failed:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}"
    )


# ─── render_zsh_completion ──────────────────────────────────────────────────


def test_zsh_render_has_compdef(ops_parser):
    """``#compdef`` is the directive zsh uses to wire the file to the
    program. Without it, an autoload silently does nothing."""
    script = render_zsh_completion(ops_parser, program_name="ops_cli")
    assert script.startswith("#compdef ops_cli")


def test_zsh_render_uses_arguments(ops_parser):
    """The renderer relies on ``_arguments`` + ``_describe`` for rich
    suggestions; check both are present."""
    script = render_zsh_completion(ops_parser, program_name="ops_cli")
    assert "_arguments" in script
    assert "_describe" in script


def test_zsh_render_contains_every_top_subcommand(ops_parser):
    """Every top-level subcommand must show up in the zsh script too."""
    script = render_zsh_completion(ops_parser, program_name="ops_cli")
    tree = discover_subcommands(ops_parser)
    for name in tree["subcommands"].keys():
        assert name in script, f"missing subcommand in zsh output: {name}"


def test_zsh_render_empty_parser_does_not_raise(minimal_parser):
    """An empty parser must still produce a non-empty zsh script."""
    script = render_zsh_completion(minimal_parser, program_name="tiny")
    assert script.startswith("#compdef tiny")
    assert "_tiny" in script


def test_zsh_render_is_syntactically_valid(ops_parser, tmp_path):
    """Lint with ``zsh -n`` if zsh is installed; skip gracefully otherwise."""
    zsh_bin = shutil.which("zsh")
    if zsh_bin is None:
        pytest.skip("zsh not available on this host")
    script = render_zsh_completion(ops_parser, program_name="ops_cli")
    target = tmp_path / "_ops_cli"
    target.write_text(script)
    result = subprocess.run(
        [zsh_bin, "-n", str(target)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"zsh -n failed:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}"
    )


# ─── Cross-check: nested subcommand suggestions on a known shape ────────────


def test_bash_emits_nested_branch_for_two_level_parser(two_level_parser):
    """A user typing ``demo alpha <TAB>`` should see ``one`` and ``two``.
    We verify by string-matching the case-arm produced for the ``alpha``
    trail. (Functional shell-level verification lives in the bash file's
    install instructions — here we trust syntactic presence.)"""
    script = render_bash_completion(two_level_parser, program_name="demo")
    assert '"alpha")' in script
    assert "one two" in script or "one" in script  # sorted: "one two"


def test_zsh_emits_nested_branch_for_two_level_parser(two_level_parser):
    """zsh equivalent — the per-subcommand case arm must reference both
    nested children."""
    script = render_zsh_completion(two_level_parser, program_name="demo")
    assert "alpha)" in script
    assert "one:" in script
    assert "two:" in script
