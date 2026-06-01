"""tools/openapi_cli.py — write / validate the OpenAPI spec from the CLI.

Usage:

    python -m tools.openapi_cli json     [--out PATH]
    python -m tools.openapi_cli yaml     [--out PATH]
    python -m tools.openapi_cli validate

Defaults the output path to ``./docs/openapi.{json,yaml}`` so a repeat
invocation overwrites the committed artifact in-place. ``validate``
exits non-zero when the structural checks in
:func:`tools.openapi_gen.validate_spec` flag any errors so CI can use
``python -m tools.openapi_cli validate`` as a regression gate.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from tools.openapi_gen import (
    build_openapi_spec,
    render_openapi_json,
    render_openapi_yaml,
    validate_spec,
)


# Default on-disk locations. We keep the spec under ``docs/`` so it
# travels with the repo and shows up alongside the prose deployment
# guide — the API surface IS user-facing documentation.
_DEFAULT_JSON_PATH = Path("docs") / "openapi.json"
_DEFAULT_YAML_PATH = Path("docs") / "openapi.yaml"


def _write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` after ensuring the parent dir exists.

    Using ``Path.write_text`` instead of ``open(...).write`` keeps the
    intent compact and avoids the temporary file handle that lints would
    flag.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def cmd_json(args: argparse.Namespace) -> int:
    """Emit the spec as JSON to ``args.out`` (or ``docs/openapi.json``).

    Returns the process exit code (0 on success).
    """
    out_path = Path(args.out) if args.out else _DEFAULT_JSON_PATH
    spec = build_openapi_spec()
    body = render_openapi_json(spec)
    _write(out_path, body)
    print(f"wrote {out_path} ({len(body):,} bytes)")
    return 0


def cmd_yaml(args: argparse.Namespace) -> int:
    """Emit the spec as YAML to ``args.out`` (or ``docs/openapi.yaml``)."""
    out_path = Path(args.out) if args.out else _DEFAULT_YAML_PATH
    spec = build_openapi_spec()
    body = render_openapi_yaml(spec)
    _write(out_path, body)
    print(f"wrote {out_path} ({len(body):,} bytes)")
    return 0


def cmd_validate(_args: argparse.Namespace) -> int:
    """Run the structural smoke-validation on the in-memory spec.

    Exits with code 0 on success, 1 on any validation error — each
    error is printed to stderr so CI logs surface them in the failure
    summary.
    """
    spec = build_openapi_spec()
    errors = validate_spec(spec)
    if errors:
        print(f"validation FAILED ({len(errors)} errors):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    # Surface a few sanity counters so the operator sees the spec
    # actually has the expected breadth — a successful exit doesn't
    # mean much if the spec is empty.
    n_paths = len(spec.get("paths") or {})
    n_operations = sum(
        len(methods) for methods in (spec.get("paths") or {}).values()
    )
    n_schemas = len((spec.get("components") or {}).get("schemas") or {})
    print(
        f"validation OK: {n_paths} paths, {n_operations} operations, "
        f"{n_schemas} schemas"
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.openapi_cli",
        description="Generate / validate the Ship Tracker OpenAPI 3.0 spec.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_json = sub.add_parser("json", help="Write the spec as JSON (default: docs/openapi.json).")
    p_json.add_argument("--out", default=None, help="Output path (default: docs/openapi.json).")
    p_json.set_defaults(func=cmd_json)

    p_yaml = sub.add_parser("yaml", help="Write the spec as YAML (default: docs/openapi.yaml).")
    p_yaml.add_argument("--out", default=None, help="Output path (default: docs/openapi.yaml).")
    p_yaml.set_defaults(func=cmd_yaml)

    p_val = sub.add_parser("validate", help="Smoke-validate the spec (exits non-zero on error).")
    p_val.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
