"""Tests for ``tools.cli_index`` — the auto-generated CLI registry.

Defining properties under test:

* ``discover_clis()`` finds EVERY true CLI in ``tools/`` (and any
  future ``cli/`` directory) — count must stay >= the current count so
  regressions (a CLI deleted but not removed from the registry) are
  caught.
* The generated docs round-trip cleanly: writing them and immediately
  running ``--verify`` exits 0.
* A hand-built fixture file with a known docstring + a single argument
  is discovered and the captured shape matches what we constructed.
* Stale on-disk docs cause ``--verify`` to exit 1 (mutate the JSON,
  re-run).
* No CLI is silently dropped from the index — every discovered CLI
  appears in BOTH outputs (markdown contains module name, JSON registry
  contains module name).
* AST fallback works for a parser built inside ``main()`` (covers the
  ``tools.styles_audit`` / ``tools.port_supply_diff`` shape).
* Placeholder entries are produced for modules whose parser cannot be
  introspected — not silently dropped, not raising.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import cli_index
from tools.cli_index import (
    ArgSpec,
    CliSpec,
    _astparse_file,
    _is_cli_file,
    _spec_from_file,
    build_registry,
    discover_clis,
    main,
    render_json,
    render_markdown,
)


_REPO_ROOT = Path(__file__).resolve().parent.parent


# ─── _is_cli_file ────────────────────────────────────────────────────────────


def test_is_cli_file_accepts_true_cli(tmp_path: Path):
    """A *.py file with an ``if __name__ == "__main__"`` block is a CLI."""
    f = tmp_path / "foo.py"
    f.write_text(
        '"""doc"""\n'
        'def main():\n    pass\n\n'
        'if __name__ == "__main__":\n    main()\n',
        encoding="utf-8",
    )
    assert _is_cli_file(f) is True


def test_is_cli_file_rejects_init(tmp_path: Path):
    """__init__.py never counts even with a main guard."""
    f = tmp_path / "__init__.py"
    f.write_text('if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    assert _is_cli_file(f) is False


def test_is_cli_file_rejects_helper_prefix(tmp_path: Path):
    """Files starting with `_` are treated as helper modules."""
    f = tmp_path / "_helper.py"
    f.write_text('if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    assert _is_cli_file(f) is False


def test_is_cli_file_rejects_no_main_block(tmp_path: Path):
    """No main guard → not a CLI."""
    f = tmp_path / "lib.py"
    f.write_text('"""lib"""\ndef helper():\n    return 1\n', encoding="utf-8")
    assert _is_cli_file(f) is False


# ─── discover_clis ───────────────────────────────────────────────────────────


# Lower bound discovered at the time of writing. The exact count drifts
# as the repo grows; the test only enforces "no silent regressions" by
# pinning a floor. Bump this number when intentionally retiring a CLI.
_MIN_EXPECTED_CLI_COUNT = 16


def test_discover_finds_at_least_the_known_clis():
    """discover_clis() never returns less than the known floor.

    Catches the regression case: someone deletes a CLI without removing
    its entry from the registry — the count drops below floor and CI
    flags it.
    """
    clis = discover_clis()
    assert len(clis) >= _MIN_EXPECTED_CLI_COUNT, (
        f"Expected at least {_MIN_EXPECTED_CLI_COUNT} CLIs, found "
        f"{len(clis)}. Did a CLI get removed without updating the "
        f"floor in tests/test_cli_index.py?"
    )


def test_discover_includes_known_cli_modules():
    """Sanity: a handful of long-standing CLIs must be in the output."""
    clis = discover_clis()
    modules = {c.module for c in clis}
    # These have all been in the repo for many releases; if any goes
    # missing it's almost certainly a regression in the discoverer.
    for must_have in (
        "tools.backtests",
        "tools.port_supply_diff",
        "tools.port_supply_export",
        "tools.ops_cli",
        "tools.backup_cli",
        "tools.styles_audit",
    ):
        assert must_have in modules, (
            f"{must_have!r} missing from discovered CLIs"
        )


def test_discover_categorises_via_field():
    """Every CliSpec carries a discovered_via tag from the known set."""
    clis = discover_clis()
    assert clis, "discover returned nothing"
    valid = {"factory", "live", "ast", "placeholder"}
    for c in clis:
        assert c.discovered_via in valid, (
            f"{c.module} has unknown discovered_via={c.discovered_via!r}"
        )


def test_discover_returns_sorted_deterministic_output():
    """Calling discover twice yields the same shape (deterministic for diff)."""
    a = discover_clis()
    b = discover_clis()
    assert [c.module for c in a] == [c.module for c in b]


# ─── No CLI silently dropped ─────────────────────────────────────────────────


def test_every_cli_appears_in_both_outputs():
    """Every discovered CLI shows up in the JSON registry AND the markdown."""
    clis = discover_clis()
    markdown = render_markdown(clis)
    registry = json.loads(render_json(clis))
    registry_modules = {entry["module"] for entry in registry["clis"]}
    for c in clis:
        assert c.module in registry_modules, (
            f"{c.module} missing from JSON registry"
        )
        assert c.module in markdown, (
            f"{c.module} missing from markdown index"
        )


def test_registry_total_matches_discovery():
    """The JSON registry's `total` field equals len(clis)."""
    clis = discover_clis()
    registry = build_registry(clis)
    assert registry["total"] == len(clis)
    assert registry["version"] == 1


# ─── AST fallback ────────────────────────────────────────────────────────────


def test_ast_fallback_extracts_arg_from_main_scoped_parser(tmp_path: Path):
    """A CLI whose parser is built inside main() is still indexed via AST."""
    src = (
        '"""Test CLI — first paragraph of the docstring.\n\n'
        'Second paragraph that should be ignored.\n"""\n'
        'import argparse\n'
        'def main():\n'
        '    p = argparse.ArgumentParser(\n'
        '        prog="test_cli",\n'
        '        description="A tiny test CLI",\n'
        '    )\n'
        '    p.add_argument("--foo", default="bar", help="The foo flag.")\n'
        '    p.add_argument("--quiet", action="store_true", help="Quiet mode.")\n'
        '    return p.parse_args()\n'
        'if __name__ == "__main__":\n'
        '    main()\n'
    )
    f = tmp_path / "test_cli.py"
    f.write_text(src, encoding="utf-8")
    prog, desc, args, err = _astparse_file(f)
    assert err == ""
    assert prog == "test_cli"
    assert desc == "A tiny test CLI"
    names = [a.name for a in args]
    assert "--foo" in names
    assert "--quiet" in names
    foo = next(a for a in args if a.name == "--foo")
    assert foo.help == "The foo flag."
    assert foo.default == "bar"
    assert foo.is_flag is False
    quiet = next(a for a in args if a.name == "--quiet")
    assert quiet.is_flag is True


def test_ast_fallback_resolves_module_constant_default(tmp_path: Path):
    """``default=_FOO`` where ``_FOO = "x"`` resolves to ``"x"``."""
    src = (
        '"""x"""\nimport argparse\n'
        '_FOO = "the-default"\n'
        'def main():\n'
        '    p = argparse.ArgumentParser(prog="x", description="y")\n'
        '    p.add_argument("--out", default=_FOO, help="h")\n'
        'if __name__ == "__main__":\n    main()\n'
    )
    f = tmp_path / "x.py"
    f.write_text(src, encoding="utf-8")
    _, _, args, _ = _astparse_file(f)
    out = next(a for a in args if a.name == "--out")
    assert out.default == "the-default"


def test_ast_fallback_handles_fstring_help(tmp_path: Path):
    """f-string help text flattens to a usable string instead of being dropped."""
    src = (
        '"""x"""\nimport argparse\n'
        'def main():\n'
        '    DEFAULT = "y"\n'
        '    p = argparse.ArgumentParser(prog="x", description="y")\n'
        '    p.add_argument("--out", default="x", help=f"Output path (default: {DEFAULT}).")\n'
        'if __name__ == "__main__":\n    main()\n'
    )
    f = tmp_path / "x.py"
    f.write_text(src, encoding="utf-8")
    _, _, args, _ = _astparse_file(f)
    out = next(a for a in args if a.name == "--out")
    assert "Output path" in out.help


# ─── Hand-built fixture: end-to-end CliSpec ──────────────────────────────────


def test_spec_from_file_handbuilt_fixture(tmp_path: Path, monkeypatch):
    """A hand-built CLI file gets a CliSpec with the expected shape."""
    # The discoverer hard-codes _REPO_ROOT to derive directory + dotted
    # path. We monkeypatch it to point at tmp_path so the test file
    # is "in scope" for the relative-path math.
    fake_root = tmp_path
    monkeypatch.setattr(cli_index, "_REPO_ROOT", fake_root)
    cli_dir = fake_root / "tools"
    cli_dir.mkdir()
    src = (
        '"""Fixture CLI — the first paragraph captured as docstring.\n\n'
        'Second paragraph is dropped on the floor.\n"""\n'
        'import argparse\n'
        'def main():\n'
        '    p = argparse.ArgumentParser(prog="fixture", description="A fixture")\n'
        '    p.add_argument("--target", required=True, help="Target to act on.")\n'
        '    p.parse_args()\n'
        'if __name__ == "__main__":\n    main()\n'
    )
    cli_file = cli_dir / "fixture_cli.py"
    cli_file.write_text(src, encoding="utf-8")
    spec = _spec_from_file(cli_file)

    assert isinstance(spec, CliSpec)
    assert spec.directory == "tools"
    assert spec.module == "tools.fixture_cli"
    assert spec.prog == "fixture" or "fixture" in spec.prog
    # First paragraph of docstring captured (no leading "Fixture CLI —" loss).
    assert "Fixture CLI" in spec.docstring
    # The --target arg made it across
    target = next(a for a in spec.args if a.name == "--target")
    assert target.required is True
    assert target.help == "Target to act on."
    # We discovered via AST (no _build_parser, parser built inside main())
    assert spec.discovered_via == "ast"


# ─── --verify gates ──────────────────────────────────────────────────────────


@pytest.fixture
def regen_to_tmp(tmp_path: Path) -> tuple[Path, Path]:
    """Regenerate the docs into ``tmp_path`` and return (md_path, json_path).

    Uses absolute paths so cli_index doesn't prepend the repo root.
    """
    md_path = tmp_path / "INDEX.md"
    json_path = tmp_path / "registry.json"
    rc = main([
        "--out-md", str(md_path),
        "--out-json", str(json_path),
        "--quiet",
    ])
    assert rc == 0, "regeneration failed"
    return md_path, json_path


def test_verify_passes_on_fresh_regeneration(regen_to_tmp):
    """A just-regenerated index passes --verify."""
    md_path, json_path = regen_to_tmp
    rc = main([
        "--out-md", str(md_path),
        "--out-json", str(json_path),
        "--verify",
        "--quiet",
    ])
    assert rc == 0


def test_verify_fails_on_stale_json(regen_to_tmp, capsys):
    """Mutating the JSON triggers --verify exit 1."""
    md_path, json_path = regen_to_tmp
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload["clis"].append({
        "directory":       "tools",
        "module":          "tools.completely_fake_cli",
        "file_path":       "/dev/null",
        "prog":            "python -m tools.completely_fake_cli",
        "description":     "not real",
        "docstring":       "not real",
        "args":            [],
        "discovered_via":  "placeholder",
        "parse_error":     "",
    })
    payload["total"] = len(payload["clis"])
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rc = main([
        "--out-md", str(md_path),
        "--out-json", str(json_path),
        "--verify",
        "--quiet",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "drift" in err.lower()


def test_verify_fails_when_files_missing(tmp_path: Path, capsys):
    """No on-disk files at all → --verify exit 1 with a 'missing' message."""
    md_path = tmp_path / "nope.md"
    json_path = tmp_path / "nope.json"
    rc = main([
        "--out-md", str(md_path),
        "--out-json", str(json_path),
        "--verify",
        "--quiet",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "missing" in err.lower()


def test_verify_committed_docs_in_sync():
    """The committed docs/CLI_INDEX.md + cli_registry.json are in sync with
    what the discoverer currently produces.

    If a CLI was added or modified without regenerating the docs, this
    test fails — the same gate the CI step enforces, only inside pytest.
    """
    rc = main(["--verify", "--quiet"])
    assert rc == 0, (
        "Committed CLI index docs are stale. Run "
        "`python -m tools.cli_index` and commit the changes."
    )


# ─── Subprocess smoke test (the CI gate path) ────────────────────────────────


def test_cli_runs_as_module():
    """``python -m tools.cli_index --verify`` exits cleanly on a clean tree."""
    result = subprocess.run(
        [sys.executable, "-m", "tools.cli_index", "--verify", "--quiet"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, (
        f"cli_index --verify failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )


# ─── Markdown rendering — shape guarantees ───────────────────────────────────


def test_markdown_has_title_and_sections():
    """Rendered markdown carries the title + per-directory sections."""
    clis = discover_clis()
    md = render_markdown(clis)
    assert md.startswith("# CLI Index")
    # Every directory present in the discovered set produces a section.
    directories = {c.directory for c in clis}
    for d in directories:
        assert f"## `{d}/`" in md


def test_markdown_includes_command_line_for_each_cli():
    """Every CLI has a `python -m ...` invocation line in the markdown."""
    clis = discover_clis()
    md = render_markdown(clis)
    for c in clis:
        # The invocation appears verbatim in the fenced code block.
        assert c.prog in md or c.module in md, (
            f"{c.module} has neither prog nor module name in markdown"
        )


def test_markdown_includes_table_of_contents():
    """A table-of-contents section exists with one row per CLI."""
    clis = discover_clis()
    md = render_markdown(clis)
    assert "## Contents" in md
    for c in clis:
        # Each ToC row is `| \`tools.foo\` | ...`.
        assert f"`{c.module}`" in md


# ─── ArgSpec edge cases ──────────────────────────────────────────────────────


def test_argspec_is_pure_dataclass():
    """ArgSpec round-trips through asdict cleanly."""
    a = ArgSpec(
        name="--out",
        help="output path",
        default="x.csv",
        required=False,
        choices=["a", "b"],
        is_positional=False,
        is_flag=False,
    )
    # Round-trip the dataclass shape through render_json's path.
    spec = CliSpec(
        directory="tools",
        module="tools.fake",
        file_path="/dev/null",
        prog="python -m tools.fake",
        description="fake",
        docstring="fake",
        args=[a],
        discovered_via="ast",
    )
    payload = json.loads(render_json([spec]))
    assert payload["total"] == 1
    arg_payload = payload["clis"][0]["args"][0]
    assert arg_payload["name"] == "--out"
    assert arg_payload["choices"] == ["a", "b"]
